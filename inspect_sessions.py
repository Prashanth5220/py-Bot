import os
from dotenv import load_dotenv
import boto3

load_dotenv()

dynamodb = boto3.resource(
    'dynamodb', 
    region_name=os.getenv('AWS_REGION', 'ap-south-2'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)
table = dynamodb.Table(os.getenv('SESSION_TABLE', 'ChatSessions'))

def inspect_sessions():
    print(f"Inspecting table '{table.table_name}'...")
    resp = table.scan(Limit=5)
    for item in resp.get('Items', []):
        sid = item.get('sessionId')
        td = item.get('tempData', {})
        print(f"Session: {sid}, Doctor: {td.get('docterId')}, Name: {td.get('doctorName')}, Token: {td.get('bookingToken')}")

if __name__ == "__main__":
    inspect_sessions()
