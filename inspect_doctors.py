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

def inspect_doctors():
    table = dynamodb.Table(os.getenv('DOCTOR_TABLE', 'Doctors'))
    print(f"Inspecting table '{table.table_name}'...")
    resp = table.scan()
    for item in resp.get('Items', []):
        print(f"Doctor: {item.get('docterId')} (Name: {item.get('name')})")

if __name__ == "__main__":
    inspect_doctors()
