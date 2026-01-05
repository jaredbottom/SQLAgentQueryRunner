-- Sample Stored Procedure: run_query_file
-- This procedure reads and executes a SQL file from a network location
-- IMPORTANT: This is a sample implementation. Adjust for your security requirements.

USE [YourDatabase];
GO

CREATE OR ALTER PROCEDURE [dbo].[run_query_file]
    @FilePath NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SQL NVARCHAR(MAX) = '';
    DECLARE @FileContent TABLE (LineNumber INT IDENTITY(1,1), LineContent VARCHAR(8000));

    BEGIN TRY
        -- Method 1: Using OPENROWSET BULK (Recommended)
        -- Requires BULK INSERT permissions
        -- Does NOT require xp_cmdshell to be enabled

        DECLARE @BulkCommand NVARCHAR(4000);
        SET @BulkCommand = N'SELECT @SQL = BulkColumn FROM OPENROWSET(
            BULK ''' + @FilePath + ''',
            SINGLE_CLOB
        ) AS x';

        EXEC sp_executesql @BulkCommand, N'@SQL NVARCHAR(MAX) OUTPUT', @SQL = @SQL OUTPUT;

        -- Execute the SQL content
        IF @SQL IS NOT NULL AND LEN(@SQL) > 0
        BEGIN
            EXEC sp_executesql @SQL;
            PRINT 'Successfully executed: ' + @FilePath;
        END
        ELSE
        BEGIN
            RAISERROR('File is empty or could not be read: %s', 16, 1, @FilePath);
        END

    END TRY
    BEGIN CATCH
        -- Log the error
        DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
        DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
        DECLARE @ErrorState INT = ERROR_STATE();

        -- Re-throw the error so it can be captured by the agent job
        RAISERROR(@ErrorMessage, @ErrorSeverity, @ErrorState);
    END CATCH
END;
GO

-- Alternative Method 2: Using xp_cmdshell (Less Secure)
-- Only use if OPENROWSET is not available
-- Requires xp_cmdshell to be enabled and proper security measures

/*
CREATE OR ALTER PROCEDURE [dbo].[run_query_file]
    @FilePath NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SQL NVARCHAR(MAX) = '';
    DECLARE @Command NVARCHAR(1000);
    DECLARE @FileContent TABLE (LineContent VARCHAR(8000));

    BEGIN TRY
        -- Read file using TYPE command (Windows) or cat (Linux)
        SET @Command = 'TYPE "' + @FilePath + '"';

        INSERT INTO @FileContent (LineContent)
        EXEC xp_cmdshell @Command;

        -- Combine all lines into single SQL string
        SELECT @SQL = STRING_AGG(ISNULL(LineContent, ''), CHAR(13) + CHAR(10))
        FROM @FileContent
        WHERE LineContent IS NOT NULL;

        -- Execute the SQL
        IF @SQL IS NOT NULL AND LEN(@SQL) > 0
        BEGIN
            EXEC sp_executesql @SQL;
            PRINT 'Successfully executed: ' + @FilePath;
        END
        ELSE
        BEGIN
            RAISERROR('File is empty or could not be read: %s', 16, 1, @FilePath);
        END

    END TRY
    BEGIN CATCH
        DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
        DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
        DECLARE @ErrorState INT = ERROR_STATE();

        RAISERROR(@ErrorMessage, @ErrorSeverity, @ErrorState);
    END CATCH
END;
GO
*/

-- Grant execute permissions to appropriate roles/users
-- GRANT EXECUTE ON [dbo].[run_query_file] TO [YourSQLAgentUser];
-- GO

-- Enable OPENROWSET and BULK operations (if using Method 1)
-- Run these as a server administrator if needed:
/*
sp_configure 'show advanced options', 1;
RECONFIGURE;
sp_configure 'Ad Hoc Distributed Queries', 1;
RECONFIGURE;
*/
