FROM rasa/rasa-sdk:3.6.0

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# Switch to root to install packages
USER root
RUN pip install --no-cache-dir -r requirements.txt
# Switch back to the non-root rasa user
USER 1001

COPY actions.py /app/actions.py

CMD ["start", "--actions", "actions"]
