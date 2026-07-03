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

def is_safe_query(sql_query: str) -> bool:
    """
    Sanitize and validate that the query is read-only.
    Allows only SELECT or WITH statements, blocks stacked queries (;),
    and filters out modification keywords at the word boundary level.
    """
    stripped = sql_query.strip().upper()
    
    # The query must start with a read-only keyword
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        return False
        
    # Block stacked queries (semicolon followed by another query)
    if ";" in stripped:
        parts = stripped.split(";")
        # If any part after the first contains non-whitespace characters, reject
        for part in parts[1:]:
            if part.strip():
                return False

    # Block write/destructive keywords at word boundaries
    blacklist = {"INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "GRANT", "TRUNCATE", "REPLACE"}
    
    # Normalize query separators to extract words accurately
    normalized = stripped.replace("(", " ").replace(")", " ").replace(",", " ").replace("\n", " ").replace("\r", " ")
    words = set(normalized.split())
    
    # If any blacklisted keyword appears as an individual word, reject
    if blacklist.intersection(words):
        return False
            
    return True

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
    
    if not db or not sql:
        return jsonify({"error": "Missing 'db' or 'sql' parameter"}), 400
        
    if not is_safe_query(sql):
        return jsonify({"error": "Invalid query. Only SELECT or WITH queries are allowed."}), 400
        
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
    
    if not db or not sql:
        return Response("Error: Missing 'db' or 'sql' parameter", status=400, mimetype='text/plain')
        
    if not is_safe_query(sql):
        return Response("Error: Invalid query. Only SELECT or WITH queries are allowed.", status=400, mimetype='text/plain')
        
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
