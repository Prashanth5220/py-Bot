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

def inspect_keys():
    table = dynamodb.Table(os.getenv('TIMESLOT_TABLE', 'TimeSlots'))
    resp = table.scan(Limit=10)
    items = resp.get('Items', [])
    for item in items:
        print(f"Slot {item.get('slotId')}: Keys = {list(item.keys())}")
        if 'docterId' in item:
            print(f"  docterId: {item['docterId']}")
        if 'doctorId' in item:
            print(f"  doctorId: {item['doctorId']}") # Checking for variations

if __name__ == "__main__":
    inspect_keys()
