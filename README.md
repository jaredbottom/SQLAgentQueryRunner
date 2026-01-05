# SQL Agent Query Runner

A Python web application for executing SQL files from network locations using SQL Server Agent jobs. The application provides a user-friendly interface with file browsing, multi-select, pattern search, and email notification capabilities.

## Features

- **Traversable File Tree**: Browse network drives with a hierarchical folder structure
- **Smart Filtering**: Only displays directories containing SQL files
- **Multi-Select**: Select multiple SQL files for batch execution
- **Pattern Search**: Filter files by name using real-time search
- **Email Notifications**: Optional email alerts on job success and/or failure
- **Auto-Cleanup**: SQL Server Agent jobs automatically delete upon completion
- **Error Reporting**: Failure emails include detailed error messages

## Architecture

The application works by:
1. Scanning a configured network drive for SQL files
2. Creating SQL Server Agent jobs that call a stored procedure: `EXEC run_query_file '\\path\to\file.sql'`
3. Configuring SQL Server Agent operators and job notifications for email alerts
4. SQL Server Agent sends email notifications natively using Database Mail
5. Jobs are configured to self-delete upon completion (`@delete_level = 1`)

## Prerequisites

- Python 3.8 or higher
- SQL Server with SQL Server Agent enabled
- ODBC Driver 17 for SQL Server installed
- Access to a network drive containing SQL files
- A stored procedure named `run_query_file` that accepts a file path parameter
- SQL Server Database Mail configured (for email notifications)

### Installing ODBC Driver (if not already installed)

**Windows:**
Download and install from: https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server

**Linux (Ubuntu/Debian):**
```bash
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list > /etc/apt/sources.list.d/mssql-release.list
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql17
```

**Linux (RHEL/CentOS):**
```bash
curl https://packages.microsoft.com/config/rhel/8/prod.repo > /etc/yum.repos.d/mssql-release.repo
yum remove unixODBC-utf16 unixODBC-utf16-devel
ACCEPT_EULA=Y yum install -y msodbcsql17
```

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd SQLAgentQueryRunner
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv

   # Windows
   venv\Scripts\activate

   # Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   ```

   Edit `.env` with your settings:
   ```ini
   # SQL Server Connection
   SQL_SERVER=your_server_name
   SQL_DATABASE=msdb
   SQL_USERNAME=your_username
   SQL_PASSWORD=your_password

   # Network Drive Path
   NETWORK_DRIVE_ROOT=\\networkdrive\sql_files

   # Application Settings
   FLASK_SECRET_KEY=your-secret-key-here
   FLASK_DEBUG=False
   ```

   **Note:** Email notifications are handled natively by SQL Server Agent using Database Mail. No SMTP configuration is needed in the application.

## Required SQL Server Setup

You need a stored procedure named `run_query_file` that can execute SQL files. Here's an example implementation:

```sql
CREATE PROCEDURE run_query_file
    @FilePath NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SQL NVARCHAR(MAX);
    DECLARE @Command NVARCHAR(1000);

    -- Read the SQL file content
    -- Note: This requires enabling xp_cmdshell or using alternative methods
    -- This is a simplified example

    CREATE TABLE #FileContent (LineContent VARCHAR(8000));

    SET @Command = 'TYPE "' + @FilePath + '"';

    INSERT INTO #FileContent
    EXEC xp_cmdshell @Command;

    SELECT @SQL = STRING_AGG(LineContent, CHAR(13) + CHAR(10))
    FROM #FileContent
    WHERE LineContent IS NOT NULL;

    -- Execute the SQL
    EXEC sp_executesql @SQL;

    DROP TABLE #FileContent;
END;
```

**Important Security Note:** The above procedure uses `xp_cmdshell` which should be carefully secured. Consider alternative approaches like:
- Using OPENROWSET with BULK to read files
- Using Integration Services (SSIS) packages
- Using PowerShell-based solutions
- Pre-loading scripts into staging tables

Ensure proper permissions and security measures are in place for your specific implementation.

### Configuring SQL Server Database Mail

For email notifications to work, you must configure SQL Server Database Mail. This is a one-time setup on your SQL Server instance.

**1. Enable Database Mail:**

```sql
-- Enable Database Mail
USE msdb;
GO

EXEC sp_configure 'show advanced options', 1;
RECONFIGURE;
GO

EXEC sp_configure 'Database Mail XPs', 1;
RECONFIGURE;
GO
```

**2. Create a Database Mail Profile and Account:**

```sql
-- Create a Database Mail account
EXEC msdb.dbo.sysmail_add_account_sp
    @account_name = 'SQLAgentMailAccount',
    @description = 'Mail account for SQL Agent notifications',
    @email_address = 'sqlagent@your-domain.com',
    @display_name = 'SQL Agent',
    @mailserver_name = 'smtp.your-domain.com',
    @port = 587,
    @username = 'your-smtp-username',
    @password = 'your-smtp-password',
    @enable_ssl = 1;

-- Create a Database Mail profile
EXEC msdb.dbo.sysmail_add_profile_sp
    @profile_name = 'SQLAgentMailProfile',
    @description = 'Profile for SQL Agent notifications';

-- Add the account to the profile
EXEC msdb.dbo.sysmail_add_profileaccount_sp
    @profile_name = 'SQLAgentMailProfile',
    @account_name = 'SQLAgentMailAccount',
    @sequence_number = 1;

-- Grant access to the profile
EXEC msdb.dbo.sysmail_add_principalprofile_sp
    @profile_name = 'SQLAgentMailProfile',
    @principal_name = 'public',
    @is_default = 1;
```

**3. Configure SQL Server Agent to use Database Mail:**

```sql
-- Configure SQL Server Agent to use Database Mail
USE msdb;
GO

EXEC msdb.dbo.sp_set_sqlagent_properties
    @databasemail_profile = 'SQLAgentMailProfile',
    @use_databasemail = 1;
GO
```

**4. Restart SQL Server Agent:**

After configuring Database Mail, restart the SQL Server Agent service for changes to take effect.

**5. Test Database Mail (Optional):**

```sql
-- Send a test email
EXEC msdb.dbo.sp_send_dbmail
    @profile_name = 'SQLAgentMailProfile',
    @recipients = 'test@your-domain.com',
    @subject = 'Test Email from SQL Server',
    @body = 'This is a test email from SQL Server Database Mail.';

-- Check the mail queue status
SELECT * FROM msdb.dbo.sysmail_allitems;
SELECT * FROM msdb.dbo.sysmail_faileditems;
```

**Note:** The application automatically creates SQL Server Agent operators for each email address you specify in the web interface. The operators are named with the pattern `TempOp_<email>` and are reused for subsequent jobs.

## Usage

1. **Start the application:**
   ```bash
   python app.py
   ```

2. **Access the web interface:**
   Open your browser and navigate to:
   ```
   http://localhost:5000
   ```

3. **Execute SQL files:**
   - Browse the file tree to find your SQL files
   - Use the search box to filter files by name
   - Click checkboxes to select files for execution
   - Optionally enter an email address for notifications
   - Choose whether to receive success and/or failure emails
   - Click "Execute Selected Files" to create and run SQL Agent jobs

## API Endpoints

### GET /api/files
Retrieve the file tree structure.

**Query Parameters:**
- `pattern` (optional): Filter files by name

**Response:**
```json
{
  "name": "root",
  "path": "\\\\networkdrive\\sql_files",
  "type": "directory",
  "children": [...]
}
```

### POST /api/execute
Execute selected SQL files.

**Request Body:**
```json
{
  "files": ["\\\\path\\file1.sql", "\\\\path\\file2.sql"],
  "email_to": "user@example.com",
  "notify_on_success": true,
  "notify_on_failure": true
}
```

**Response:**
```json
{
  "success": true,
  "message": "Created 2 job(s)",
  "jobs": [
    {
      "job_id": "...",
      "job_name": "RunSQLFile_...",
      "file": "\\\\path\\file1.sql"
    }
  ]
}
```

### GET /api/job-status/<job_name>
Check the status of a specific job.

**Response:**
```json
{
  "status": "succeeded",
  "message": "Job completed successfully"
}
```

## Security Considerations

1. **SQL Injection**: The application passes file paths to SQL Server. Ensure proper validation and sanitization.

2. **Authentication**: Consider adding user authentication (e.g., Flask-Login, Active Directory integration).

3. **HTTPS**: In production, use HTTPS to encrypt traffic. Configure with a reverse proxy like nginx or Apache.

4. **Network Access**: Restrict access to the application server and ensure only authorized users can access the web interface.

5. **SQL Server Permissions**: The SQL user should have minimal required permissions:
   - Execute permissions on the `run_query_file` stored procedure
   - Permissions to create and manage SQL Agent jobs (typically requires `SQLAgentUserRole`)

6. **File System Permissions**: Ensure the application has read-only access to the network drive.

## Production Deployment

For production use, deploy with a WSGI server like Gunicorn:

1. **Install Gunicorn:**
   ```bash
   pip install gunicorn
   ```

2. **Run with Gunicorn:**
   ```bash
   gunicorn -w 4 -b 0.0.0.0:5000 app:app
   ```

3. **Use a reverse proxy (nginx example):**
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;

       location / {
           proxy_pass http://127.0.0.1:5000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

4. **Set up as a system service** (systemd example):
   ```ini
   [Unit]
   Description=SQL Agent Query Runner
   After=network.target

   [Service]
   User=your-user
   WorkingDirectory=/path/to/SQLAgentQueryRunner
   Environment="PATH=/path/to/venv/bin"
   ExecStart=/path/to/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app

   [Install]
   WantedBy=multi-user.target
   ```

## Troubleshooting

### Cannot connect to SQL Server
- Verify SQL Server connection details in `.env`
- Ensure SQL Server allows remote connections
- Check firewall rules
- Verify ODBC driver is installed

### Cannot access network drive
- Ensure the path in `NETWORK_DRIVE_ROOT` is correct
- Verify the application has permissions to access the network path
- On Linux, ensure the network share is mounted

### Jobs not auto-deleting
- Verify the job is created with `@delete_level = 1`
- Check SQL Server Agent is running
- Review SQL Server Agent job history

### Email notifications not working
- Verify SMTP settings in `.env`
- Check SMTP server allows relay from the application server
- Review application logs for email errors

## Contributing

Contributions are welcome! Please follow these guidelines:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

See LICENSE file for details.

## Support

For issues and questions, please create an issue in the repository.
