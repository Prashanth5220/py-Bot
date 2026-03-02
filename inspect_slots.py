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
table = dynamodb.Table(os.getenv('TIMESLOT_TABLE', 'TimeSlots'))

def inspect_slots():
    print(f"Inspecting TimeSlots table '{table.table_name}' for date 2026-02-24...")
    try:
        resp = table.scan(
            FilterExpression=Attr('date').eq('2026-02-24')
        )
        items = resp.get('Items', [])
        if not items:
            print("No slots found for 2026-02-24.")
            # Let's see some other slots to be sure we're reading right
            print("Scanning first 5 items in table instead:")
            resp_any = table.scan(Limit=5)
            for item in resp_any.get('Items', []):
                print(f"Found slot for date {item.get('date')}: {item.get('slotId')} (Doc: {item.get('docterId')})")
        else:
            print(f"Found {len(items)} slots for 2026-02-24:")
            for item in sorted(items, key=lambda x: x.get('startTime', '')):
                print(f"Slot: {item.get('slotId')}, Doc: {item.get('docterId')}, Time: {item.get('startTime')}-{item.get('endTime')}, Status: {item.get('status')}")
    except Exception as e:
        print(f"Error scanning table: {e}")

if __name__ == "__main__":
    inspect_slots()
