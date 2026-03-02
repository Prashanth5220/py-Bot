import os
from dotenv import load_dotenv
import boto3
from boto3.dynamodb.conditions import Attr

load_dotenv()

dynamodb = boto3.resource(
    'dynamodb', 
    region_name=os.getenv('AWS_REGION', 'ap-south-2'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)
table = dynamodb.Table(os.getenv('SESSION_TABLE', 'ChatSessions'))

def find_active_booking_sessions():
    print("Searching for sessions with an active booking token...")
    resp = table.scan(
        FilterExpression=Attr('tempData.bookingToken').exists()
    )
    items = resp.get('Items', [])
    if not items:
        print("No sessions with bookingToken found.")
    for item in items:
        td = item.get('tempData', {})
        print(f"Session: {item.get('sessionId')}")
        print(f"  Doctor: {td.get('docterId')} ({td.get('doctorName')})")
        print(f"  Token: {td.get('bookingToken')}")
        print(f"  Expiry: {td.get('tokenExpiry')}")

if __name__ == "__main__":
    find_active_booking_sessions()
