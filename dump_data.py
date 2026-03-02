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

def scan_table(table_name):
    table = dynamodb.Table(table_name)
    response = table.scan()
    return response.get('Items', [])

def dump_all():
    tables = {
        'Doctors': os.getenv('DOCTOR_TABLE', 'Doctors'),
        'Departments': os.getenv('DEPARTMENT_TABLE', 'Departments'),
        'TimeSlots': os.getenv('TIMESLOT_TABLE', 'TimeSlots'),
        'Appointments': os.getenv('APPOINTMENT_TABLE', 'Appointments')
    }
    
    for label, name in tables.items():
        print(f"\n=== {label} ({name}) ===")
        items = scan_table(name)
        for item in items:
            print(item)

if __name__ == "__main__":
    dump_all()
