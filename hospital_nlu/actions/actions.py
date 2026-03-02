import requests
from rasa_sdk import Action
from rasa_sdk.executor import CollectingDispatcher
import logging

logger = logging.getLogger(__name__)

class ActionBookAppointment(Action):

    def name(self) -> str:
        return "action_book_appointment"

    def run(self, dispatcher: CollectingDispatcher,
            tracker,
            domain):

        patient_name = tracker.get_slot("patient_name")
        doctor_name = tracker.get_slot("doctor_name")
        date = tracker.get_slot("date")

        try:
            # We use the internal docker-compose hostname 'fastapi'
            response = requests.post(
                "http://app:3000/api/appointments",
                json={
                    "patient_name": patient_name,
                    "doctor_name": doctor_name,
                    "date": date
                },
                timeout=5
            )

            if response.status_code == 200:
                # Assuming the external API returns JSON with a success flag
                data = response.json()
                if data.get("success"):
                    dispatcher.utter_message(
                        text=f"✅ Appointment booked with Dr. {doctor_name} on {date}. ID: {data.get('appointment_id')}"
                    )
                else:
                    dispatcher.utter_message(
                        text=f"❌ Failed to book appointment. Reason: {data.get('message')}"
                    )
            else:
                dispatcher.utter_message(
                    text="❌ Failed to book appointment. Please try again."
                )

        except Exception as e:
            logger.error(f"Error booking appointment from action server: {e}")
            dispatcher.utter_message(
                text="⚠️ System error while communicating with the booking database."
            )

        return []
