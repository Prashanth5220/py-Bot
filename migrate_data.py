import os
import boto3
from dotenv import load_dotenv

load_dotenv()

dynamodb = boto3.resource(
    'dynamodb', 
    region_name=os.getenv('AWS_REGION', 'ap-south-2'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)

DOC_MAPPING = {
    'doc-001': 'DOC011',
    'doc-002': 'DOC012',
    'doc-003': 'DOC013',
    'doc-004': 'DOC014',
    'doc-005': 'DOC015',
    'doc-006': 'DOC016',
}

DEPT_MAPPING = {
    'dept-001': 'DEPT001',
    'dept-002': 'DEPT003',
    'dept-003': 'DEPT002',
}

DOCTOR_TABLE = os.getenv('DOCTOR_TABLE', 'Doctors')
DEPT_TABLE = os.getenv('DEPARTMENT_TABLE', 'Departments')
SLOT_TABLE = os.getenv('TIMESLOT_TABLE', 'TimeSlots')
APPT_TABLE = os.getenv('APPT_TABLE', 'Appointments')
SESSION_TABLE = os.getenv('SESSION_TABLE', 'ChatSessions')

def migrate():
    # 1. Migrate Doctors
    print("Migrating Doctors...")
    doc_table = dynamodb.Table(DOCTOR_TABLE)
    for old_id, new_id in DOC_MAPPING.items():
        resp = doc_table.get_item(Key={'docterId': old_id})
        if 'Item' in resp:
            item = resp['Item']
            item['docterId'] = new_id
            # Map department if needed
            if item.get('departmentId') in DEPT_MAPPING:
                item['departmentId'] = DEPT_MAPPING[item['departmentId']]
            doc_table.put_item(Item=item)
            doc_table.delete_item(Key={'docterId': old_id})
            print(f"  Moved {old_id} -> {new_id} ({item.get('name')})")

    # 2. Update TimeSlots
    print("Updating TimeSlots...")
    slot_table = dynamodb.Table(SLOT_TABLE)
    slots = slot_table.scan().get('Items', [])
    for slot in slots:
        old_doc_id = slot.get('docterId')
        if old_doc_id in DOC_MAPPING:
            new_doc_id = DOC_MAPPING[old_doc_id]
            slot_table.update_item(
                Key={'slotId': slot['slotId']},
                UpdateExpression="SET docterId = :d",
                ExpressionAttributeValues={':d': new_doc_id}
            )
            print(f"  Updated slot {slot['slotId']}: {old_doc_id} -> {new_doc_id}")

    # 3. Update Appointments
    print("Updating Appointments...")
    appt_table = dynamodb.Table(APPT_TABLE)
    appts = appt_table.scan().get('Items', [])
    for appt in appts:
        old_doc_id = appt.get('docterId')
        changed = False
        updates = []
        vals = {}
        names = {}
        
        if old_doc_id in DOC_MAPPING:
            new_doc_id = DOC_MAPPING[old_doc_id]
            updates.append("docterId = :did")
            vals[':did'] = new_doc_id
            changed = True
            
        if updates:
            appt_table.update_item(
                Key={'appointmentId': appt['appointmentId']},
                UpdateExpression="SET " + ", ".join(updates),
                ExpressionAttributeValues=vals
            )
            print(f"  Updated Appointment {appt['appointmentId']}")

    # 4. Clean up Departments
    print("Cleaning up Departments...")
    dept_table = dynamodb.Table(DEPT_TABLE)
    for old_id in DEPT_MAPPING.keys():
        dept_table.delete_item(Key={'departmentId': old_id})
        print(f"  Deleted redundant dept {old_id}")

    print("\nMigration Complete!")

if __name__ == "__main__":
    migrate()
