import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from src.services.agent_service import agent_service

async def test_agent():
    print("Testing LangChain Agent Initialization...")
    await agent_service.warm_up()
    
    print("\nTest 1: General Query")
    response = await agent_service.handle_message("Hi, who are you?", "test_user_1")
    print(f"Agent: {response[0]}")
    
    print("\nTest 2: Search Doctors Tool")
    response = await agent_service.handle_message("Do you have any cardiologists?", "test_user_1")
    print(f"Agent: {response[0]}")
    
    print("\nTest 3: Get Slots Tool")
    response = await agent_service.handle_message("What times is Dr. Sharma available tomorrow?", "test_user_1")
    print(f"Agent: {response[0]}")

if __name__ == "__main__":
    asyncio.run(test_agent())
