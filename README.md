# AWS Lambda Cron-based Attendance Sync to Zoho People

This project contains source code and supporting files for a **serverless application** that periodically syncs attendance records from an on-premises (or external) device to **Zoho People**. The application is written in **Python 3.12** and is deployed using the AWS Serverless Application Model (SAM). The key AWS resources in this stack include a **Lambda function** (scheduled every 5 minutes) and an **IAM role** with permissions to access **SSM Parameter Store**.

## Project Structure

- **sync_function/** - Contains the Python source code for the Lambda function (`sync/app.py`, etc.).

- **template.yaml** - The SAM template that defines the Lambda function, IAM role, and scheduled event (every 5 minutes cron job).

- **.env.example** - A sample environment file containing the necessary environment variables. Copy or rename this to `.env` and replace with your actual values before deploying.

- **Makefile** - Simplifies build and deploy operations using the `.env` file.

## Deployment Prerequisites

Before deploying, ensure you have the following tools installed and configured:

- **SAM CLI** - [Install the SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html)

- **Docker** - [Install Docker Community Edition](https://hub.docker.com/search/?type=edition&offering=community) for building dependencies in a containerized environment.

- **Python 3.12** - If you wish to run and test the code locally.

- **AWS CLI** - [Install AWS CLI](https://aws.amazon.com/cli/) and configure your credentials (`aws configure`).

- **.env** - Copy `.env.example` to `.env` and add your actual parameters (DB_HOST, DB_USER, DB_PASSWORD, etc.).

## Environment Variables

You should have the following variables defined in your `.env` file:

```bash
DB_HOST="<your-database-host>"
DB_USER="<your-database-username>"
DB_PASSWORD="<your-database-password>"
DB_NAME="<your-database-name>"
ZOHO_CLIENT_ID="<your-zoho-client-id>"
ZOHO_CLIENT_SECRET="<your-zoho-client-secret>"
ZOHO_REFRESH_TOKEN="<your-zoho-refresh-token>"
```

These values are passed to the Lambda at deploy time, as shown in the **Makefile**.

## Building and Deploying

This project is managed through a **Makefile**, which references your local `.env` to supply parameters to AWS SAM. The typical workflow is:

1. **Copy the environment file**  

   ```bash
   cp .env.example .env
   ```

   Fill in the correct values for your environment variables.
2. **Build and Deploy**

    ```bash
    make
    ```

    This runs:

    - ```sam build --beta-features```
    - ```sam deploy --stack-name SyncService ...```

    using the environment variables from `.env`. By default, it will create or update a CloudFormation stack named SyncService.

## Manual SAM Commands

If you prefer to deploy manually:
1. **Build the Project**  

   ```bash
   sam build --beta-features
   ```

    This will install any dependencies (if needed) and prepare the Lambda code.
2. **Deploy the Project**

    ```bash
    sam build
    ```

    SAM will prompt for a stack name, region, and other parameters. You can also specify your parameters manually using the `--parameter-overrides` flag.

## Local Development and Testing
1. **Local Build** 

   ```bash
   sam build --beta-features
   ```

   Ensures that dependencies are installed and your code is compiled (if needed).
2. **Local Invoke**

    You can locally test your function by invoking it directly with sam local invoke. For example:
    
    ```bash
    sam local invoke SyncFunction
    ```
    
    If your function needs environment variables in local testing, create a local-env.json or use the `--env-vars` option.
    
    > **Note:** Since this function relies on an external database and Zoho People, a full local test may require mocking or stubbing these external calls.

3. **View Logs Locally**

    While the function is running locally, logs will print to your terminal.

## Monitoring & Logs in AWS
After deployment, you can view logs in AWS CloudWatch:

```bash
sam logs -n SyncFunction --stack-name SyncService --tail
```

Replace `SyncService` with your actual CloudFormation stack name if you changed it during deployment.

## Cleanup
To remove all AWS resources deployed by this stack, you can use:

```bash
sam delete --stack-name SyncService
```
    
Or, if using the Makefile approach:

```bash
aws cloudformation delete-stack --stack-name SyncService
```

Make sure to replace `SyncService` with the name of your CloudFormation stack if you chose a different name.

## Resources

- [AWS SAM Developer Guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/what-is-sam.html)

- [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html)

- [AWS Serverless Application Repository](https://aws.amazon.com/serverless/serverlessrepo/)
