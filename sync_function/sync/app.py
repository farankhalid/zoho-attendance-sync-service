import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pymysql
import requests
import boto3

logger = logging.getLogger()
# logger.setLevel(logging.DEBUG)

# Environment Variables
DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN")
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET")

# SSM Parameter Names
ZOHO_TOKEN_SSM_KEY = "ZOHO_ACCESS_TOKEN"
ZOHO_TOKEN_EXPIRY_SSM_KEY = "ZOHO_ACCESS_TOKEN_EXPIRY"

# Zoho Endpoints and Config
ZOHO_TOKEN_ENDPOINT = "https://accounts.zoho.eu/oauth/v2/token"
ZOHO_BULK_IMPORT_ENDPOINT = "https://people.zoho.eu/people/api/attendance/bulkImport"
ACCESS_TOKEN_GRACE_PERIOD = 300

# Define Pakistan timezone as UTC+5
PKT = timezone(timedelta(hours=5))

ssm = boto3.client("ssm")


def lambda_handler(event, context):
    # logger.info("Received event: %s", json.dumps(event))
    try:
        logger.info("Calling fetch_attendance_records...")
        records = fetch_attendance_records(DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
        logger.info("Fetched %d records from database.", len(records))
        
        if records:
            logger.info("First few records: %s", json.dumps(records[:5], default=str))
        else:
            logger.info("No records retrieved from the database.")

        logger.info("Completed fetch_attendance_records successfully.")

        if not records:
            logger.info("No records found to send to Zoho.")
            return success_response("No data to send.")

        logger.info("Transforming records for Zoho...")
        zoho_data = transform_records_for_zoho(records)
        logger.info("Transformed Zoho data. Number of records: %d", len(zoho_data))
        if zoho_data:
            logger.info("Transformed records: %s", json.dumps(zoho_data))

        logger.info("Retrieving or refreshing Zoho access token...")
        access_token = get_or_refresh_zoho_access_token(ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET)
        logger.info("Using access token: %s...", access_token[:10])

        logger.info("Sending data to Zoho...")
        response = send_to_zoho(zoho_data, access_token)
        logger.info("Zoho API response: %s", response)
        logger.info("Data sent to Zoho successfully.")

        return success_response("Data sent to Zoho successfully", response)

    except Exception as e:
        logger.exception("An error occurred while processing.")
        return error_response(str(e))


def fetch_attendance_records(db_host, db_user, db_password, db_name):
    logger.info("Calculating time range for attendance records...")
    end_time = datetime.now(PKT)
    start_time = end_time - timedelta(minutes=1500)

    start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Start time (PKT) is: %s", start_time_str)
    end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("End time (PKT) is: %s", end_time_str)

    query = f"""
    SELECT *
        FROM (
            SELECT 
                pe.emp_code AS employeeId,
                DATE_FORMAT(apt.clock_in, '%Y-%m-%d %H:%i:%s') AS eventTime,
                '1' AS isCheckin,
                DATE_FORMAT(apt.clock_in, '%Y-%m-%d %H:%i:%s') AS downloadDate
            FROM zkbiotime.att_payloadtimecard apt
            JOIN zkbiotime.personnel_employee pe ON pe.id = apt.emp_id
            WHERE apt.clock_in >= STR_TO_DATE('{start_time_str}', '%Y-%m-%d %H:%i:%s')
            AND apt.clock_in < STR_TO_DATE('{end_time_str}', '%Y-%m-%d %H:%i:%s')
            AND apt.clock_in IS NOT NULL

            UNION ALL

            SELECT 
                pe.emp_code AS employeeId,
                DATE_FORMAT(apt.clock_out, '%Y-%m-%d %H:%i:%s') AS eventTime,
                '0' AS isCheckin,
                DATE_FORMAT(apt.clock_out, '%Y-%m-%d %H:%i:%s') AS downloadDate
            FROM zkbiotime.att_payloadtimecard apt
            JOIN zkbiotime.personnel_employee pe ON pe.id = apt.emp_id
            WHERE apt.clock_out >= STR_TO_DATE('{start_time_str}', '%Y-%m-%d %H:%i:%s')
            AND apt.clock_out < STR_TO_DATE('{end_time_str}', '%Y-%m-%d %H:%i:%s')
            AND apt.clock_out IS NOT NULL
        ) AS attendance
        ORDER BY eventTime;
    """

    logger.info("Connecting to DB at host: %s", db_host)
    connection = pymysql.connect(
        host=db_host, port=3300, user=db_user, password=db_password, database=db_name
    )
    try:
        logger.info("Executing query: %s", query)
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query)
            records = cursor.fetchall()
            logger.info("Query executed successfully, fetched %s.", records)
            return records
    finally:
        connection.close()
        logger.info("Closed DB connection.")


def transform_records_for_zoho(records):
    zoho_data = []
    for record in records:
        if record["isCheckin"] == "1":
            zoho_data.append({"empId": record["employeeId"], "checkIn": record["eventTime"]})
        else:
            zoho_data.append({"empId": record["employeeId"], "checkOut": record["eventTime"]})
    return zoho_data


def get_or_refresh_zoho_access_token(refresh_token, client_id, client_secret):
    logger.info("Attempting to retrieve cached access token and expiry from SSM.")
    try:
        access_token = get_ssm_parameter(ZOHO_TOKEN_SSM_KEY)
        expiry_str = get_ssm_parameter(ZOHO_TOKEN_EXPIRY_SSM_KEY)
        expiry_time = datetime.fromisoformat(expiry_str)
        logger.info("Cached access token expires at: %s", expiry_time.isoformat())

        current_time = datetime.now(timezone.utc)
        if current_time < (expiry_time - timedelta(seconds=ACCESS_TOKEN_GRACE_PERIOD)):
            logger.info("Using cached Zoho access token. Valid until %s", expiry_time.isoformat())
            return access_token
        else:
            logger.info("Cached token near expiry, refreshing now.")
            return refresh_zoho_access_token(refresh_token, client_id, client_secret)
    except ssm.exceptions.ParameterNotFound:
        logger.info("No cached Zoho token found. Generating a new one via refresh.")
        return refresh_zoho_access_token(refresh_token, client_id, client_secret)
    except Exception as e:
        logger.error("Error retrieving cached token: %s", e)
        logger.info("Refreshing token due to error.")
        return refresh_zoho_access_token(refresh_token, client_id, client_secret)


def refresh_zoho_access_token(refresh_token, client_id, client_secret):
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

    # logger.info("Refreshing Zoho access token with payload (masking secrets): {"
    #              f"'grant_type': 'refresh_token', 'client_id': '{client_id[:5]}...', "
    #              f"'client_secret': '***', 'refresh_token': '{refresh_token[:5]}...'")

    response = requests.post(ZOHO_TOKEN_ENDPOINT, data=payload)
    logger.info("Zoho token refresh response status: %d", response.status_code)
    logger.info("Zoho token refresh response body: %s", response.text)

    if response.status_code != 200:
        raise Exception(f"Failed to refresh Zoho access token: {response.text}")

    tokens = response.json()
    access_token = tokens["access_token"]
    expiry_time = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])

    logger.info("Storing new access token and expiry in SSM...")
    put_ssm_parameter(ZOHO_TOKEN_SSM_KEY, access_token, param_type="SecureString")
    put_ssm_parameter(ZOHO_TOKEN_EXPIRY_SSM_KEY, expiry_time.isoformat(), param_type="String")

    logger.info("Access token refreshed and cached until %s", expiry_time.isoformat())
    return access_token


def send_to_zoho(data, access_token):
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
    }

    payload = {
        "data": json.dumps(data),
        "dateFormat": "yyyy-MM-dd HH:mm:ss"
    }

    logger.info("Sending POST to Zoho bulkImport endpoint with form-data payload: %s", payload)

    try:
        response = requests.post(ZOHO_BULK_IMPORT_ENDPOINT, headers=headers, data=payload)

        logger.info("Zoho bulkImport response status: %d", response.status_code)
        logger.info("Zoho bulkImport response body: %s", response.text)

        return {
            "status_code": response.status_code,
            "response_body": response.text
        }
    except Exception as e:
        logger.error("Error while sending data to Zoho: %s", str(e))
        raise Exception(f"Failed to send data to Zoho: {str(e)}")


def get_ssm_parameter(name):
    logger.info("Retrieving SSM parameter: %s", name)
    value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    logger.info("Retrieved SSM parameter: %s = %s", name, value[:10] + "..." if len(value) > 10 else value)
    return value


def put_ssm_parameter(name, value, param_type="String"):
    logger.info("Putting SSM parameter: %s = %s...", name, value[:10] + "..." if len(value) > 10 else value)
    ssm.put_parameter(Name=name, Value=value, Type=param_type, Overwrite=True)
    logger.info("Successfully updated SSM parameter: %s", name)


def success_response(message, data=None):
    body = {"message": message}
    if data is not None:
        body["data"] = data
    logger.info("Returning success response: %s", json.dumps(body))
    return {
        "statusCode": 200,
        "body": json.dumps(body)
    }


def error_response(message):
    logger.info("Returning error response: %s", message)
    return {
        "statusCode": 500,
        "body": json.dumps({"error": message})
    }
