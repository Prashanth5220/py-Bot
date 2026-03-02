import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from src.services.agent_service import agent_service

async def main():
    from src.services.agent_service import ChatGoogleGenerativeAI, search_doctors, get_doctor_slots, book_appointment
    try:
        llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", temperature=0.2)
        res = llm.invoke("hello")
        print("Success! gemini-flash-latest exists. Res: " + str(res.content))
    except Exception as e:
        print(f"Error: {e}")



if __name__ == "__main__":
    asyncio.run(main())
