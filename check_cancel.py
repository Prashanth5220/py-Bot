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

def check_cancel_data():
    appt_table = dynamodb.Table(os.getenv('APPOINTMENT_TABLE', 'Appointments'))
    slot_table = dynamodb.Table(os.getenv('TIMESLOT_TABLE', 'TimeSlots'))

    print("--- CANCELED APPOINTMENTS (RECENT) ---")
    # Scan for canceled appointments on 2026-02-26
    resp = appt_table.scan(
        FilterExpression=Attr('status').eq('CANCELED') & Attr('date').eq('2026-02-26')
    )
    appts = resp.get('Items', [])
    for appt in appts:
        print(f"Appt: {appt.get('appointmentId')}, Dr: {appt.get('docterId')}, Slot: {appt.get('slotId')}")
        
        # Check the slot state
        sid = appt.get('slotId')
        if sid:
            s_resp = slot_table.get_item(Key={'slotId': sid})
            slot = s_resp.get('Item')
            if slot:
                print(f"  -> Slot {sid} Status: {slot.get('status')} | Date: {slot.get('date')} | Time: {slot.get('startTime')}-{slot.get('endTime')}")
            else:
                print(f"  -> Slot {sid} NOT FOUND")
    
    if not appts:
        print("No canceled appointments found for 2026-02-26 in DB.")

if __name__ == "__main__":
    check_cancel_data()
