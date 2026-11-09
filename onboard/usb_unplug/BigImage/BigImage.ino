// T1-39: about 1 MB of image, so an upload is still running when the person
// unplugs the cable.
OPENPLC_APP_VERSION(1, 0, 0);

#define BIG (1000U * 1000U)
static const uint8_t big[BIG] = {1, 2, 3};

void setup() {
  Serial.begin(115200);
}

void loop() {
  static uint32_t i;
  Serial.println(big[i++ % BIG]);
  delay(500);
}
