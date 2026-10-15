# 2. Connect your warehouse

Step 2 of 5 — setup step 1, **Your database or warehouse**. At the end of it, the portal has
your connection settings stored. Nothing has contacted the warehouse yet.
{ .ax-step }

The form says: *"Stored in praxis.db, which is owner-readable only, and written into the file you run
the deployment with. Nothing is contacted yet."*


<div class="ui">
--8<-- "docs/.snippets/ui-setup.html"
<div class="ui-main narrow">
<div class="ui-h2">Your database or warehouse</div>
<div class="ui-chips"><span class="ui-chip on">PostgreSQL</span><span class="ui-chip">MySQL</span><span class="ui-chip">SQL Server</span><span class="ui-chip">Snowflake</span><span class="ui-chip">BigQuery</span><span class="ui-chip">Databricks</span><span class="ui-chip">…</span></div>
<div class="ui-grid2">
<label class="ui-field"><span>Host <b>*</b></span><span class="ui-input">host.docker.internal</span></label>
<label class="ui-field"><span>Port</span><span class="ui-input">5432</span></label>
<label class="ui-field"><span>Database <b>*</b></span><span class="ui-input focus">retail</span></label>
<label class="ui-field"><span>User <b>*</b></span><span class="ui-input">praxis</span></label>
<label class="ui-field"><span>Password</span><span class="ui-input">••••••••••</span></label>
<label class="ui-field"><span>Description · optional</span><span class="ui-input">Store sales and stock, daily</span></label>
</div>
<p class="ui-faint">▸ More settings</p>
<p class="ui-faint">Stored in praxis.db, which is owner-readable only, and written into the file you run the deployment with. Nothing is contacted yet.</p>
<span class="ui-btn primary">Store the connection</span>
</div>
</div>
<p class="ui-caption">A Postgres database on this machine, as Docker reaches it: <code>host.docker.internal</code>, not <code>localhost</code>.</p>

## Pick the type

| Databases | Warehouses and lakehouses |
|---|---|
| **PostgreSQL** — Postgres, or anything speaking its protocol | **Snowflake** — an account, a warehouse and a database |
| **MySQL** — MySQL or MariaDB | **BigQuery** — a Google Cloud project and a service account |
| **SQL Server** | **Databricks** — a SQL warehouse's JDBC URL and a token |
| **ClickHouse** — over HTTP | **Redshift** |
| **DuckDB** — a file, or MotherDuck | **Athena** — query S3 |
| | **Trino** — Trino or Presto |

## Fields

Required fields are marked; optional ones say **optional**. Less common settings are under
**More settings**. Every type also has an optional **Description**, shown on the Data page and not
read by the agent.

=== "Postgres / MySQL / SQL Server / Redshift"

    - **Host** — starts as `host.docker.internal`. A database on this machine is
      `host.docker.internal` from inside a container, never `localhost`.
    - **Port** — 5432 / 3306 / 1433 / 5439
    - **Database**, **User**, **Password**
    - More settings: **Encrypt the connection**, **Server certificate** (paste a PEM), and for SQL
      Server **Windows domain**

=== "Snowflake"

    - **Account identifier** — like `xy12345.eu-central-1`, without `.snowflakecomputing.com`
    - **Warehouse** — the compute that runs the queries, not the database
    - **Database**, **User**, **Password**
    - More settings: **Role**, **Sign in with** (password, key pair, OAuth, password and MFA)

=== "BigQuery"

    - **Project** — the project ID, not its display name
    - **Service account key** — paste the whole JSON. If the key belongs to a different project,
      the form offers **Use X**
    - More settings: **Dataset location** (e.g. `EU`)

=== "Databricks"

    - **JDBC URL** — starts with `jdbc:`
    - **Access token**
    - **Accept the Databricks driver licence** — the driver will not load without it
    - **Database**; more settings: **Catalog**

=== "Athena"

    - **Access key ID**, **Secret access key**, **Region**
    - **Where results are written** — an `s3://` location
    - More settings: **Workgroup**

=== "ClickHouse / Trino / DuckDB"

    - ClickHouse: **Host**, Port 8123, Database, User, Password
    - Trino: **Host**, Port 8080, **User**, Password, **Catalog**, Schema
    - DuckDB: **Database file** (empty means in-memory); more settings: **MotherDuck token**

**Other settings** takes any extra `CUBEJS_DB_*` / `CUBEJS_AWS_*` setting by name, for a
warehouse option the form does not list.

Click **Store the connection**. If something required is blank, the form says
*Still needed: …*. When it is stored, setup step 2 opens.

**Next:** [3. Start the services](services.md)

## Changing it later

**Data → … → Edit connection** opens the same form. Leave a box empty to keep what is stored.
After changing credentials, download the semantic layer's compose file again and run
`docker compose up -d` with it — the running services still have the old values.

**Test connection** on the Data page checks that both services answer. It does not prove the
warehouse password: the first thing that connects to the warehouse is reading its tables.
