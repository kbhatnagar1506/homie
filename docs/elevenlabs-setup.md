# Real phone calls: ElevenLabs Agents + Twilio

1. **Twilio:** buy a US number with Voice at https://console.twilio.com.
2. **ElevenLabs → Agents → Phone Numbers:** import the Twilio number (native Twilio integration). Copy its **phone number ID** → `ELEVENLABS_PHONE_NUMBER_ID`.
3. **ElevenLabs → Agents → Create agent** ("Homie Caller"). Copy the **agent ID** → `ELEVENLABS_AGENT_ID`.
   - **First message:** `Hi, this is Homie, an AI assistant calling on behalf of a student. Is this {{building_name}}?`
   - **System prompt:**
     ```
     You are Homie, an AI assistant calling a leasing office on behalf of an international student.
     Always be clear you are an AI assistant if asked. Be polite, brief, and specific.
     Your task on this call: {{task}}
     Get concrete numbers (price, fees, dates). Repeat key numbers back to confirm.
     When you have what you need, thank them and end the call.
     ```
   - **Dynamic variables:** `building_name`, `task` (Homie fills these per call).
   - **Analysis → Data collection** (optional, Homie also extracts from the transcript): `price`, `discount`, `discount_day`, `ssn_alternative`, `fees`, `matched`, `payment`, `repair_slot`.
4. Put the phones that play each office in `.env` (`PHONE_MAPLE_COURT=+1...` etc.), set `MOCK_CALLS=0`, restart the agents.
