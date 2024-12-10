import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pymysql
import requests
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

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
ZOHO_BULK_IMPORT_ENDPOINT = "https://people.zoho.com/people/api/attendance/bulkImport"
ACCESS_TOKEN_GRACE_PERIOD = 300  # 5 minutes before expiry

# Time window for fetching attendance records (e.g., past 15 minutes)
TIME_DELTA_MINUTES = 15

ssm = boto3.client("ssm")

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    try:
        # Fetch attendance records from DB
        records = fetch_attendance_records(DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, TIME_DELTA_MINUTES)
        logger.info("Fetched %d records from database.", len(records))

        if not records:
            return success_response("No data to send.")

        # Transform data to Zoho format
        zoho_data = transform_records_for_zoho(records)

        # Retrieve or refresh Zoho access token using the refresh token
        access_token = get_or_refresh_zoho_access_token(ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET)

        # Send data to Zoho
        response = send_to_zoho(zoho_data, access_token)
        logger.info("Zoho API response: %s", response)

        return success_response("Data sent to Zoho successfully", response)

    except Exception as e:
        logger.exception("An error occurred.")
        return error_response(str(e))


def fetch_attendance_records(db_host, db_user, db_password, db_name, delta_minutes):
    """
    Fetch attendance records from the database within the last `delta_minutes`.
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=delta_minutes)
    start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

    query = f"""
    SELECT *
    FROM (
        SELECT 
            pe.emp_code AS employeeId,
            DATE_FORMAT(apt.clock_in, '%Y-%m-%d %H:%i:%s') AS eventTime,
            '1' AS isCheckin
        FROM zkbiotime.att_payloadtimecard apt
        JOIN zkbiotime.personnel_employee pe ON pe.id = apt.emp_id
        WHERE apt.clock_in >= STR_TO_DATE('{start_time_str}', '%Y-%m-%d %H:%i:%s')
          AND apt.clock_in < STR_TO_DATE('{end_time_str}', '%Y-%m-%d %H:%i:%s')
          AND apt.clock_in IS NOT NULL

        UNION

        SELECT 
            pe.emp_code AS employeeId,
            DATE_FORMAT(apt.clock_out, '%Y-%m-%d %H:%i:%s') AS eventTime,
            '0' AS isCheckin
        FROM zkbiotime.att_payloadtimecard apt
        JOIN zkbiotime.personnel_employee pe ON pe.id = apt.emp_id
        WHERE apt.clock_out >= STR_TO_DATE('{start_time_str}', '%Y-%m-%d %H:%i:%s')
          AND apt.clock_out < STR_TO_DATE('{end_time_str}', '%Y-%m-%d %H:%i:%s')
          AND apt.clock_out IS NOT NULL
    ) AS attendance;
    """

    connection = pymysql.connect(
        host=db_host, user=db_user, password=db_password, database=db_name
    )
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query)
            return cursor.fetchall()
    finally:
        connection.close()


def transform_records_for_zoho(records):
    """
    Transform database records into the format required by Zoho.
    """
    zoho_data = []
    for record in records:
        if record["isCheckin"] == "1":
            zoho_data.append({"empId": record["employeeId"], "checkIn": record["eventTime"]})
        else:
            zoho_data.append({"empId": record["employeeId"], "checkOut": record["eventTime"]})
    return zoho_data


def get_or_refresh_zoho_access_token(refresh_token, client_id, client_secret):
    """
    Retrieve a cached access token if it's still valid. Otherwise, refresh it.
    """
    try:
        access_token = get_ssm_parameter(ZOHO_TOKEN_SSM_KEY)
        expiry_str = get_ssm_parameter(ZOHO_TOKEN_EXPIRY_SSM_KEY)
        expiry_time = datetime.fromisoformat(expiry_str)

        # Check if token is still valid with a grace period
        if datetime.now(timezone.utc) < (expiry_time - timedelta(seconds=ACCESS_TOKEN_GRACE_PERIOD)):
            logger.info("Using cached Zoho access token. Valid until %s", expiry_time.isoformat())
            return access_token
        else:
            logger.info("Cached token near expiry, refreshing now.")
            return refresh_zoho_access_token(refresh_token, client_id, client_secret)
    except ssm.exceptions.ParameterNotFound:
        logger.info("No cached Zoho token found. Generating a new one.")
        return refresh_zoho_access_token(refresh_token, client_id, client_secret)
    except Exception as e:
        logger.error("Error retrieving cached token: %s", e)
        return refresh_zoho_access_token(refresh_token, client_id, client_secret)


def refresh_zoho_access_token(refresh_token, client_id, client_secret):
    """
    Refresh the Zoho access token using the refresh token.
    """
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

    response = requests.post(ZOHO_TOKEN_ENDPOINT, data=payload)
    if response.status_code != 200:
        raise Exception(f"Failed to refresh Zoho access token: {response.text}")

    tokens = response.json()
    access_token = tokens["access_token"]
    expiry_time = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])

    # Store new token and expiry in SSM
    put_ssm_parameter(ZOHO_TOKEN_SSM_KEY, access_token, param_type="SecureString")
    put_ssm_parameter(ZOHO_TOKEN_EXPIRY_SSM_KEY, expiry_time.isoformat(), param_type="String")

    logger.info("Access token refreshed and cached until %s", expiry_time.isoformat())
    return access_token


def send_to_zoho(data, access_token):
    """
    Send the transformed data to Zoho's Bulk Import API.
    """
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"data": data, "dateFormat": "yyyy-MM-dd HH:mm:ss"}
    response = requests.post(ZOHO_BULK_IMPORT_ENDPOINT, headers=headers, json=payload)
    return {
        "status_code": response.status_code,
        "response_body": response.text
    }


def get_ssm_parameter(name):
    """
    Retrieve a parameter from SSM Parameter Store (with decryption).
    """
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def put_ssm_parameter(name, value, param_type="String"):
    """
    Put a parameter into SSM Parameter Store.
    """
    ssm.put_parameter(Name=name, Value=value, Type=param_type, Overwrite=True)


def success_response(message, data=None):
    body = {"message": message}
    if data is not None:
        body["data"] = data
    return {
        "statusCode": 200,
        "body": json.dumps(body)
    }


def error_response(message):
    return {
        "statusCode": 500,
        "body": json.dumps({"error": message})
    }