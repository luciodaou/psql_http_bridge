import os
import io
import csv
import re
from flask import Flask, request, jsonify, Response
import pg8000.dbapi

app = Flask(__name__)

# Load DB configuration from environment variables
DB_HOST = os.environ.get('DB_HOST')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_USER = os.environ.get('DB_READONLY_USER')
DB_PASSWORD = os.environ.get('DB_READONLY_PASSWORD')

def parse_allowlist(*keys):
    """
    Parse comma-separated allowlists from environment variables.
    Uniform rule for all ALLOWED_* / alias settings:
    If not declared, empty, '*', or 'all', access is unrestricted (returns empty set).
    """
    for key in keys:
        if key in os.environ:
            raw = os.environ[key].strip()
            # If empty, '*', or 'all', treat as unrestricted
            if not raw or raw in ('*', 'all'):
                return set()
            items = [item.strip().lower() for item in raw.split(',') if item.strip()]
            if '*' in items or 'all' in items:
                return set()
            if items:
                return set(items)
    return set()

# Load access control allowlists
ALLOWED_DATABASES = parse_allowlist('ALLOWED_DATABASES', 'database', 'DATABASE', 'DB_NAME')
ALLOWED_SCHEMAS = parse_allowlist('ALLOWED_SCHEMAS', 'schema', 'SCHEMA')
ALLOWED_TABLES = parse_allowlist('ALLOWED_TABLES', 'tables', 'TABLES')

def strip_comments_and_strings(sql: str) -> str:
    # Remove multiline comments /* ... */
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.DOTALL)
    # Remove single line comments -- ...
    sql = re.sub(r'--[^\r\n]*', ' ', sql)
    # Replace string literals '...' with ''
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return sql

def unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith('"') and identifier.endswith('"') and len(identifier) >= 2:
        return identifier[1:-1]
    return identifier

def validate_schema_and_tables(cleaned_sql: str) -> tuple[bool, str | None]:
    if not ALLOWED_SCHEMAS and not ALLOWED_TABLES:
        return True, None

    # Collect CTE names defined in WITH ... AS (
    cte_names = set()
    with_matches = re.findall(r'\bWITH\s+([a-zA-Z0-9_"]+)\s+AS|\b,\s*([a-zA-Z0-9_"]+)\s+AS', cleaned_sql, re.IGNORECASE)
    for m in with_matches:
        cte = unquote(m[0] or m[1]).lower()
        if cte:
            cte_names.add(cte)

    def check_target(token: str) -> tuple[bool, str | None]:
        token_clean = token.strip('();, \t\n\r')
        if not token_clean or token_clean.startswith('('):
            return True, None
            
        parts = token_clean.split('.')
        if len(parts) == 1:
            table = unquote(parts[0]).lower()
            if table in cte_names:
                return True, None
            if ALLOWED_TABLES and table not in ALLOWED_TABLES:
                return False, f"Access to table '{table}' is not allowed."
        elif len(parts) >= 2:
            schema = unquote(parts[0]).lower()
            table = unquote(parts[1]).lower()
            if ALLOWED_SCHEMAS and schema not in ALLOWED_SCHEMAS:
                return False, f"Access to schema '{schema}' is not allowed."
            if ALLOWED_TABLES and table not in ALLOWED_TABLES and f"{schema}.{table}" not in ALLOWED_TABLES:
                return False, f"Access to table '{table}' is not allowed."
        return True, None

    # 1. Check any 3-part or more identifier anywhere in the query (e.g. schema.table.column)
    three_part_matches = re.finditer(r'(\b[a-zA-Z0-9_]+|"[^"]+")\.(\b[a-zA-Z0-9_]+|"[^"]+")\.(\b[a-zA-Z0-9_]+|"[^"]+")', cleaned_sql)
    for m in three_part_matches:
        schema = unquote(m.group(1)).lower()
        table = unquote(m.group(2)).lower()
        if ALLOWED_SCHEMAS and schema not in ALLOWED_SCHEMAS:
            return False, f"Access to schema '{schema}' is not allowed."
        if ALLOWED_TABLES and table not in ALLOWED_TABLES and f"{schema}.{table}" not in ALLOWED_TABLES:
            return False, f"Access to table '{table}' is not allowed."

    # 2. Check targets directly after JOIN
    join_matches = re.finditer(r'\bJOIN\s+([a-zA-Z0-9_".]+)', cleaned_sql, re.IGNORECASE)
    for m in join_matches:
        ok, err = check_target(m.group(1))
        if not ok:
            return False, err

    # 3. Check targets in FROM clause (handles single table and comma-separated: FROM t1, t2)
    from_clauses = re.finditer(r'\bFROM\s+([^;()]+?)(?=\bWHERE\b|\bGROUP\b|\bHAVING\b|\bORDER\b|\bLIMIT\b|\bOFFSET\b|\bUNION\b|\bJOIN\b|\bON\b|\bWINDOW\b|\)|$)', cleaned_sql, re.IGNORECASE)
    for fc in from_clauses:
        raw_list = fc.group(1)
        for item in raw_list.split(','):
            tokens = item.strip().split()
            if tokens:
                first_token = tokens[0]
                ok, err = check_target(first_token)
                if not ok:
                    return False, err

    return True, None

def is_safe_query(sql_query: str) -> tuple[bool, str | None]:
    """
    Sanitize and validate that the query is read-only and targets allowed schemas/tables.
    Allows only SELECT or WITH statements, blocks stacked queries (;),
    filters out modification keywords at the word boundary level,
    and validates schema and table permissions.
    """
    stripped = sql_query.strip()
    upper_query = stripped.upper()
    
    # The query must start with a read-only keyword
    if not (upper_query.startswith("SELECT") or upper_query.startswith("WITH")):
        return False, "Invalid query. Only SELECT or WITH queries are allowed."
        
    # Block stacked queries (semicolon followed by another query)
    if ";" in stripped:
        parts = stripped.split(";")
        for part in parts[1:]:
            if part.strip():
                return False, "Query stacking (multiple statements separated by ';') is not allowed."

    # Block write/destructive keywords at word boundaries
    blacklist = {"INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "GRANT", "TRUNCATE", "REPLACE"}
    
    cleaned_sql = strip_comments_and_strings(stripped)
    normalized = cleaned_sql.upper().replace("(", " ").replace(")", " ").replace(",", " ").replace("\n", " ").replace("\r", " ")
    words = set(normalized.split())
    
    if blacklist.intersection(words):
        blocked = ", ".join(sorted(blacklist.intersection(words)))
        return False, f"Destructive SQL operations are not allowed (found: {blocked})."
            
    # Validate schemas and tables
    target_ok, target_err = validate_schema_and_tables(cleaned_sql)
    if not target_ok:
        return False, target_err

    return True, None

def clean_whitespace(val):
    """Collapse any 1 or more whitespaces into a single space and strip."""
    if isinstance(val, str):
        return re.sub(r'\s+', ' ', val).strip()
    return val

def execute_sql(database: str, sql: str):
    conn = None
    try:
        conn = pg8000.dbapi.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            user=DB_USER,
            password=DB_PASSWORD,
            database=database
        )
        cursor = conn.cursor()
        
        # Enforce search_path if allowed schemas are configured
        if ALLOWED_SCHEMAS:
            quoted_schemas = ", ".join(f'"{s}"' for s in ALLOWED_SCHEMAS)
            cursor.execute(f"SET search_path TO {quoted_schemas};")

        cursor.execute(sql)
        
        # Extract columns and rows
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        
        # Clean whitespace for string fields
        cleaned_rows = [tuple(clean_whitespace(val) for val in row) for row in rows]
        
        return columns, cleaned_rows, None
    except Exception as e:
        return None, None, str(e)
    finally:
        if conn:
            conn.close()

@app.route('/query', methods=['GET'])
def query_json():
    db = request.args.get('db')
    sql = request.args.get('sql')
    
    # Fallback to single allowed database if db parameter is omitted
    if not db and ALLOWED_DATABASES and len(ALLOWED_DATABASES) == 1:
        db = next(iter(ALLOWED_DATABASES))
        
    if not db or not sql:
        return jsonify({"error": "Missing 'db' or 'sql' parameter"}), 400
        
    if ALLOWED_DATABASES and db.strip().lower() not in ALLOWED_DATABASES:
        return jsonify({"error": f"Access to database '{db}' is not allowed."}), 403
        
    safe, error_msg = is_safe_query(sql)
    if not safe:
        return jsonify({"error": error_msg or "Invalid query."}), 400
        
    columns, rows, error = execute_sql(db, sql)
    if error:
        return jsonify({"error": error}), 500
        
    # Map row tuples to dicts using column names
    result = [dict(zip(columns, row)) for row in rows]
    return jsonify(result)

@app.route('/csv', methods=['GET'])
def query_csv():
    db = request.args.get('db')
    sql = request.args.get('sql')
    
    # Fallback to single allowed database if db parameter is omitted
    if not db and ALLOWED_DATABASES and len(ALLOWED_DATABASES) == 1:
        db = next(iter(ALLOWED_DATABASES))
        
    if not db or not sql:
        return Response("Error: Missing 'db' or 'sql' parameter", status=400, mimetype='text/plain')
        
    if ALLOWED_DATABASES and db.strip().lower() not in ALLOWED_DATABASES:
        return Response(f"Error: Access to database '{db}' is not allowed.", status=403, mimetype='text/plain')
        
    safe, error_msg = is_safe_query(sql)
    if not safe:
        return Response(f"Error: {error_msg or 'Invalid query.'}", status=400, mimetype='text/plain')
        
    columns, rows, error = execute_sql(db, sql)
    if error:
        return Response(f"Error: {error}", status=500, mimetype='text/plain')
        
    # Generate CSV payload in memory
    si = io.StringIO()
    si.write('\ufeff')  # Add BOM for Excel
    cw = csv.writer(si, quoting=csv.QUOTE_ALL)
    cw.writerow(columns)
    cw.writerows(rows)
    
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=query_results.csv"}
    )

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"})

if __name__ == '__main__':
    # Listen on port 13714
    app.run(host='0.0.0.0', port=13714)
