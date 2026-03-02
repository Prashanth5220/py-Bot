import asyncio
from src.services.dynamodb import _users_table, scan_all

async def main():
    users = await scan_all(_users_table)
    print(f"Total Users: {len(users)}")
    if users:
        print(users[0])
        print(users[-1])

if __name__ == "__main__":
    asyncio.run(main())
