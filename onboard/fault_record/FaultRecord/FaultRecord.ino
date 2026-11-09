// T3-26: 'F' on USB Serial writes to an unmapped address, raising a BusFault.
// The core logs [sys][FAULT] and resets; run_fault_record.py reads it back.
OPENPLC_APP_VERSION(1, 0, 0);

void setup() {
  Serial.begin(115200);
}

void loop() {
  if (Serial.available() > 0 && Serial.read() == 'F') {
    *(volatile uint32_t *)0xA0000000UL = 1U;   // reserved in the H743 memory map
  }
  delay(10);
}
