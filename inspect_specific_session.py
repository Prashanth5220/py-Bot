import os
from dotenv import load_dotenv
import boto3
import json

load_dotenv()

dynamodb = boto3.resource(
    'dynamodb', 
    region_name=os.getenv('AWS_REGION', 'ap-south-2'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)
table = dynamodb.Table(os.getenv('SESSION_TABLE', 'ChatSessions'))

def inspect_session(sid):
    resp = table.get_item(Key={'sessionId': sid})
    item = resp.get('Item')
    if item:
        print(f"Session {sid}:")
        print(json.dumps(item, indent=2, default=str))
    else:
        print(f"Session {sid} NOT FOUND.")

if __name__ == "__main__":
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else '7306093352'
    inspect_session(sid)
