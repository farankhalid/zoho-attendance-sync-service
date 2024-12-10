import pymysql
import json
import requests
from datetime import datetime, timedelta
import os
import boto3

ssm = boto3.client("ssm")


def lambda_handler(event, context):
    print("Event JSON:", event)
    # Environment Variables
    db_host = os.environ["DB_HOST"]
    db_user = os.environ["DB_USER"]
    db_password = os.environ["DB_PASSWORD"]
    db_name = os.environ["DB_NAME"]
    refresh_token = os.environ["ZOHO_REFRESH_TOKEN"]
    client_id = os.environ["ZOHO_CLIENT_ID"]
    client_secret = os.environ["ZOHO_CLIENT_SECRET"]

    # Get a valid access token
    access_token = get_cached_access_token(refresh_token, client_id, client_secret)

    # Calculate time range for the query (last 15 minutes)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=15)
    start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

    # MySQL Query
    query = f"""
    SELECT *
    FROM (
        -- Check-in Records
        SELECT 
            personnel_employee.emp_code AS employeeId,
            DATE_FORMAT(att_payloadtimecard.clock_in, '%Y-%m-%d %H:%i:%s') AS eventTime,
            '1' AS isCheckin
        FROM 
            zkbiotime.att_payloadtimecard
        JOIN 
            zkbiotime.personnel_employee 
        ON 
            personnel_employee.id = att_payloadtimecard.emp_id
        WHERE 
            att_payloadtimecard.clock_in >= STR_TO_DATE('{start_time_str}', '%Y-%m-%d %H:%i:%s')
            AND att_payloadtimecard.clock_in < STR_TO_DATE('{end_time_str}', '%Y-%m-%d %H:%i:%s')
            AND att_payloadtimecard.clock_in IS NOT NULL

        UNION

        -- Check-out Records
        SELECT 
            personnel_employee.emp_code AS employeeId,
            DATE_FORMAT(att_payloadtimecard.clock_out, '%Y-%m-%d %H:%i:%s') AS eventTime,
            '0' AS isCheckin
        FROM 
            zkbiotime.att_payloadtimecard
        JOIN 
            zkbiotime.personnel_employee 
        ON 
            personnel_employee.id = att_payloadtimecard.emp_id
        WHERE 
            att_payloadtimecard.clock_out >= STR_TO_DATE('{start_time_str}', '%Y-%m-%d %H:%i:%s')
            AND att_payloadtimecard.clock_out < STR_TO_DATE('{end_time_str}', '%Y-%m-%d %H:%i:%s')
            AND att_payloadtimecard.clock_out IS NOT NULL
    ) AS attendance;
    """

    try:
        # Connect to the database
        connection = pymysql.connect(
            host=db_host, user=db_user, password=db_password, database=db_name
        )
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query)
            records = cursor.fetchall()

        # Transform data into Zoho's required format
        zoho_data = []
        for record in records:
            if record["isCheckin"] == "1":
                zoho_data.append(
                    {
                        "empId": record["employeeId"],
                        "checkIn": record["eventTime"]
                    }
                )
            elif record["isCheckin"] == "0":
                zoho_data.append(
                    {
                        "empId": record["employeeId"],
                        "checkOut": record["eventTime"]
                    }
                )

        # Send data to Zoho API
        if zoho_data:
            response = send_to_zoho(zoho_data, access_token)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": "Data sent to Zoho successfully", "response": response}
                ),
            }
        else:
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No data to send"}),
            }

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"message": str(e)})}
    finally:
        if "connection" in locals():
            connection.close()


def get_cached_access_token(refresh_token, client_id, client_secret):
    """Retrieve a cached access token or generate a new one if expired."""
    try:
        # Retrieve cached token and expiry time from SSM
        access_token = ssm.get_parameter(Name="ZOHO_ACCESS_TOKEN", WithDecryption=True)["Parameter"]["Value"]
        expiry_time = ssm.get_parameter(Name="ZOHO_ACCESS_TOKEN_EXPIRY", WithDecryption=True)["Parameter"]["Value"]

        # Check if the token is still valid
        if datetime.utcnow() < datetime.fromisoformat(expiry_time):
            print("Using cached access token.")
            return access_token
    except ssm.exceptions.ParameterNotFound:
        print("No cached token found. Generating a new one.")

    # Generate a new access token
    return refresh_access_token(refresh_token, client_id, client_secret)


def refresh_access_token(refresh_token, client_id, client_secret):
    """Refresh the access token using the refresh token."""
    url = "https://accounts.zoho.com/oauth/v2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        tokens = response.json()
        access_token = tokens["access_token"]
        expiry_time = datetime.utcnow() + timedelta(seconds=tokens["expires_in"])

        # Save new token and expiry time to SSM
        ssm.put_parameter(
            Name="ZOHO_ACCESS_TOKEN", Value=access_token, Type="String", Overwrite=True
        )
        ssm.put_parameter(
            Name="ZOHO_ACCESS_TOKEN_EXPIRY",
            Value=expiry_time.isoformat(),
            Type="String",
            Overwrite=True,
        )

        print("Access token refreshed and cached.")
        return access_token
    else:
        raise Exception(f"Failed to refresh access token: {response.text}")


def send_to_zoho(data, access_token):
    """Send data to Zoho Bulk Import API."""
    url = "https://people.zoho.com/people/api/attendance/bulkImport"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"data": data, "dateFormat": "yyyy-MM-dd HH:mm:ss"}
    response = requests.post(url, headers=headers, json=payload)
    return {"status_code": response.status_code, "response_body": response.text}