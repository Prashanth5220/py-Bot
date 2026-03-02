import logging
from typing import List
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.tools import tool

from src.services import dynamodb as db
from src.utils.crypto import new_uuid

logger = logging.getLogger(__name__)

# ── 1. Define Tools for the Agent ─────────────────────────────────────────────

@tool
async def search_doctors(department_name: str) -> str:
    """Use this tool to find available doctors for a specific department (e.g., Cardiology, General Medicine)."""
    try:
        depts = await db.get_all_departments()
        dept = next((d for d in depts if department_name.lower() in d["name"].lower()), None)
        if not dept:
            return f"No department found matching '{department_name}'."
        
        docs = await db.get_doctors_by_department(dept["departmentId"])
        if not docs:
            return f"No doctors currently available in {dept['name']}."
            
        doc_list = [f"Dr. {d['name']} ({d.get('specialization', 'General')}) - ID: {d.get('docterId', d.get('doctorId'))}" for d in docs]
        return f"Found {len(doc_list)} doctors in {dept['name']}:\n" + "\n".join(doc_list)
    except Exception as e:
        logger.error(f"Error in search_doctors tool: {e}")
        return "An error occurred while searching for doctors."

@tool
async def get_doctor_slots(doctor_id: str, date: str) -> str:
    """
    Use this tool to get available time slots for a specific doctor on a specific date.
    Date must be in YYYY-MM-DD format.
    """
    try:
        slots = await db.get_available_slots_for_doctor_on_date(doctor_id, date)
        if not slots:
            return f"No available slots found for doctor {doctor_id} on {date}."
            
        slot_list = [f"Slot ID: {s['slotId']} | Time: {s.get('startTime')} - {s.get('endTime')}" for s in slots]
        return f"Found {len(slot_list)} available slots:\n" + "\n".join(slot_list)
    except Exception as e:
        logger.error(f"Error in get_doctor_slots tool: {e}")
        return "An error occurred while fetching available time slots."

@tool
async def book_appointment(doctor_id: str, doctor_name: str, date: str, slot_id: str, start_time: str, end_time: str, patient_name: str, patient_phone: str) -> str:
    """
    Use this tool to finalize a booking AFTER the user has explicitly confirmed the time slot.
    """
    try:
        # 1. Mark the slot as booked
        await db.book_slot(slot_id)
        
        # 2. Create the appointment record
        appt_id = new_uuid()
        appointment = {
            "appointmentId": appt_id,
            "patientName": patient_name,
            "userPhone": patient_phone,
            "docterId": doctor_id,
            "doctor": doctor_name,
            "date": date,
            "time": f"{start_time} - {end_time}",
            "slotId": slot_id,
            "status": "BOOKED",
        }
        await db.put_appointment(appointment)
        return f"Booking Confirmed! Appointment ID is {appt_id}."
    except Exception as e:
        logger.error(f"Error in book_appointment tool: {e}")
        return "Booking failed. The slot may have already been taken."

# ── 2. Agent Initialization ───────────────────────────────────────────────────

class AgentService:
    _instance = None

    def __init__(self):
        # We initialize the real agent asynchronously in warm_up
        self.agent_executor = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def warm_up(self):
        """Initializes the LangChain OpenAI agent with tools."""
        logger.info("🤖 Warming up LangChain AI Agent...")
        try:
            llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", temperature=0.2)
            tools = [search_doctors, get_doctor_slots, book_appointment]
            
            system_message = (
                 "You are the friendly and professional Healix Hospital Chatbot reception AI. "
                 "Your goal is to help users find doctors and book appointments. "
                 "Always be polite, concise, and helpful. "
                 "1. If they ask for a doctor, use 'search_doctors'. "
                 "2. If they want a time, use 'get_doctor_slots'. You must ask for a date (YYYY-MM-DD) if missing. "
                 "3. DO NOT book an appointment until the user explicitly confirms the exact time slot. "
                 "4. When booking, use 'book_appointment'. Note: You need the user's name and phone number to book. If you do not have them, tell the user to type '/start' and complete the official Registration/Login flow first."
                 "\nKeep your responses short and readable for WhatsApp/Telegram."
            )
            
            # Using langgraph's prebuilt react agent which is the modern standard for tool-calling models
            self.agent_executor = create_react_agent(llm, tools, prompt=system_message)
            logger.info("✅ LangChain Agent ready.")
        except Exception as e:
            logger.error(f"Failed to initialize Agent: {e}")

    async def handle_message(self, text: str, sender_id: str, session: dict = None) -> List[str]:
        """
        Sends a message to the LangChain Agent.
        We pass the user's context (name/phone) as part of the system prompt if available.
        """
        if not self.agent_executor:
            return ["I'm currently starting up. Please try again in a few seconds!"]
            
        try:
            # We can inject context about the logged-in user to help the agent
            user_context = ""
            if session and session.get("role") == "USER":
                user_context = f"\n[System Context: The current logged-in user is {session.get('tempData', {}).get('name', 'Patient')} with phone {session.get('userPhone', 'unknown')}]."
                
            input_text = text + user_context
            
            # create_react_agent expects a state dict with "messages"
            response = await self.agent_executor.ainvoke({"messages": [HumanMessage(content=input_text)]})
            
            # The last message in the response is the agent's output
            agent_output = response["messages"][-1].content
            
            # Handle cases where the model returns a list of blocks instead of a string
            if isinstance(agent_output, list):
                text_blocks = [blk.get("text", "") for blk in agent_output if isinstance(blk, dict) and "text" in blk]
                agent_output = "\n".join(text_blocks) if text_blocks else str(agent_output)
                
            return [agent_output]
        except Exception as e:
            logger.error(f"Error in Agent reasoning: {str(e)}")
            return ["I encountered an unexpected error while thinking. Please try again later."]

agent_service = AgentService.get_instance()
