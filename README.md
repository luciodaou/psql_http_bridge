# WSL2 Driverless PostgreSQL Connection Bridge

This directory contains a lightweight Python web service running inside Docker (WSL2 Fedora 44) that allows Excel on your Windows host to query a remote PostgreSQL database **without installing any database drivers** on Windows.

## Architecture

```
[ Excel (Windows Host) ]
         │
         ▼ (HTTP GET http://localhost:13714/csv?db=...&sql=...)
[ Docker / Python Web Service (WSL2) ]
         │
         ▼ (Native PostgreSQL connection via pg8000)
[ PostgreSQL Server (your-db-host.example.com:5432) ]
```

- **Local Web Service Port**: `13714` (bound strictly to `127.0.0.1` for security)
- **Remote Host**: `your-db-host.example.com`
- **Remote Port**: `5432`

---

## How to Run

1. **Verify `.env`**: Make sure the `.env` file contains your target database connection details:
   ```env
   DB_HOST=your-db-host.example.com
   DB_PORT=5432
   DB_READONLY_USER=your_readonly_user
   DB_READONLY_PASSWORD=your_password
   ```

2. **Start the Bridge**:
   Run the following command to build the image and start the container in the background:
   ```bash
   docker compose up -d --build
   ```

3. **Check Container Status**:
   ```bash
   docker compose ps
   ```

4. **Stop the Bridge**:
   ```bash
   docker compose down
   ```

---

## Web API Endpoints

The service runs at `http://localhost:13714` and offers two query endpoints. Both endpoints accept the following query parameters:
* `db`: The name of the database to query.
* `sql`: The SQL SELECT or WITH query to execute.

### 1. JSON Endpoint (`/query`)
Executes the SQL query and returns results in JSON format.
* **Example URL**: `http://localhost:13714/query?db=my_database&sql=SELECT+*+FROM+users+LIMIT+10`

### 2. CSV Endpoint (`/csv`)
Executes the SQL query and returns results as a downloadable CSV file. This is the **most recommended** endpoint for Excel as Excel handles CSV import natively.
* **Example URL**: `http://localhost:13714/csv?db=my_database&sql=SELECT+*+FROM+users+LIMIT+10`

*Note: For security, only read-only queries (`SELECT` or `WITH`) are allowed. Destructive statements like `DROP`, `DELETE`, or query stacking via multiple semicolons are automatically blocked.*

---

## Loading Data into Excel (Zero Drivers Required)

Since WSL2 mirrors `localhost` to the Windows host, you can fetch data directly via Excel's native web data import:

### Importing as CSV (Recommended)

1. Open Excel on Windows.
2. Go to the **Data** tab -> **From Web** (in the "Get & Transform Data" section).
3. Select **Basic** and paste your query URL. For example:
   ```http
   http://localhost:13714/csv?db=your_database_name&sql=SELECT * FROM your_table LIMIT 100
   ```
   *(Make sure to URL-encode any special characters in the query parameter, or let Excel's import wizard handle it automatically).*
4. Click **OK**.
5. Excel will connect, fetch the CSV, and show a preview.
6. Click **Load** to import the query results directly into your spreadsheet!
