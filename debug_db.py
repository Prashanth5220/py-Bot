import asyncio
import os
import sys
from pprint import pprint

# Add src to path
sys.path.append(os.path.abspath("."))

from src.services import dynamodb as db

async def main():
    try:
        print("--- Doctors Table Content ---")
        doctors = await db.scan_all(db._doctors_table)
        pprint(doctors)
        
        print("\n--- Searching for 'John' ---")
        matched = [d for d in doctors if "John" in d.get("name", "")]
        pprint(matched)
        
        print("\n--- Appointments for Naveen ---")
        # Assuming we can find Naveen's phone or search all
        appts = await db.scan_all(db._appointments_table)
        naveen_appts = [a for a in appts if "Naveen" in a.get("patientName", "")]
        pprint(naveen_appts)
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
