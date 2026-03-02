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

def find_specific_slots():
    print("Searching for DOC001 slots on 2026-02-24...")
    resp = table.scan(
        FilterExpression=Attr('docterId').eq('DOC001') & Attr('date').eq('2026-02-24')
    )
    items = resp.get('Items', [])
    if not items:
        print("No slots found for DOC001 on 2026-02-24.")
    for item in items:
        print(f"Slot: {item.get('slotId')}, Time: {item.get('startTime')}-{item.get('endTime')}, Status: {item.get('status')}")

    print("\nSearching for SLOT015...")
    resp2 = table.get_item(Key={'slotId': 'SLOT015'})
    item2 = resp2.get('Item')
    if item2:
        print(f"Found SLOT015: Date={item2.get('date')}, Doc={item2.get('docterId')}, Time={item2.get('startTime')}-{item2.get('endTime')}, Status={item2.get('status')}")
    else:
        print("SLOT015 NOT found by Key.")

if __name__ == "__main__":
    find_specific_slots()
