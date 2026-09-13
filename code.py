import storage
import json
import board
import displayio
import time
import rgbmatrix
import framebufferio
import wifi
import socketpool
import ssl
import adafruit_requests
from custom_font import CUSTOM_FONT
import adafruit_json_stream as json_stream
import gc


displayio.release_displays()

# === WI-FI PODEŠAVANJA ===
WIFI_SSID = "Đukić"
WIFI_PASSWORD = "AB36B36A"

def connect_wifi():
    print(f"Povezivanje na Wi-Fi: {WIFI_SSID}...")
    while not wifi.radio.ipv4_address:
        try:
            wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
            print(f"Povezan! IP: {wifi.radio.ipv4_address}")
        except Exception as e:
            print(f"Povezivanje neuspešno ({e}), pokušavam ponovo za 3s...")
            time.sleep(3)

connect_wifi()

# === INICIJALIZACIJA HUB75 128x64 DISPLEJA ===
matrix = rgbmatrix.RGBMatrix(
    width=128,
    height=64,
    bit_depth=6,
    rgb_pins=[
        board.GP2,   # R1
        board.GP3,   # G1
        board.GP6,   # B1
        board.GP7,   # R2
        board.GP8,   # G2
        board.GP9    # B2
    ],
    addr_pins=[
        board.GP10,  # A
        board.GP16,  # B
        board.GP18,  # C
        board.GP20,  # D
        board.GP21   # E
    ],
    clock_pin=board.GP11,
    latch_pin=board.GP12,
    output_enable_pin=board.GP13,
    tile=1,
    serpentine=False,
    doublebuffer=True,
)

display = framebufferio.FramebufferDisplay(matrix)

# === CUSTOM FONT DEFINICIJA (Skraćeno na prva 3 karaktera) ===



# === MREŽA I REQUESTS (SA SSL PODRŠKOM) ===
pool = socketpool.SocketPool(wifi.radio)
ssl_context = ssl.create_default_context()
requests = adafruit_requests.Session(pool, ssl_context)

# === API KONFIGURACIJA ===
ANNOUNCEMENT_URL = "https://online.bgnaplata.rs/sr/announcement_arrival/"
ANNOUNCEMENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "sr,en;q=0.9,sr-RS;q=0.8,en-US;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://online.bgnaplata.rs",
    "X-Requested-With": "XMLHttpRequest",
}

STATION_IDS = ["20867", "20868", "20939", "22365", "22910"]

STATIONS = {
    "20867": "Kanarevo brdo",
    "20939": "OŠ Đura Jakšić",
    "22365": "Miljakovac /Pijaca/",
    "20040": "Vareška",
    "22909": "Vareška",
    "22910": "Vareška",
}

ANNOUNCEMENT_BASE_DATA = {
    "b": "TS001831",
    "d": "2",
    "c": "1498",
    "s": "1",
}

# === PODEŠAVANJE DISPLAYIO SCENE I RASTERA ===
main_group = displayio.Group()
display.root_group = main_group

palette = displayio.Palette(2)
palette[0] = 0x000000  # Pozadina (crno)
palette[1] = 0xFF0000  # Boja teksta (ćilibar)

screen_bitmap = displayio.Bitmap(128, 64, 2)
tile_grid = displayio.TileGrid(screen_bitmap, pixel_shader=palette)
main_group.append(tile_grid)

LINE_HEIGHT = 12

def draw_custom_char(bitmap, char_str, x_off, y_off):
    coords = CUSTOM_FONT.get(char_str, [])
    max_x = 0
    for px, py in coords:
        bx = x_off + px
        by = y_off + py
        if 0 <= bx < 128 and 0 <= by < 64:
            bitmap[bx, by] = 1
        if px > max_x:
            max_x = px
    return max_x + 1 if coords else 4

def draw_custom_string(bitmap, text, x, y):
    curr_x = x
    for char in text:
        if char == " ":
            curr_x += 4
        else:
            w = draw_custom_char(bitmap, char, curr_x, y)
            curr_x += w + 1
    return curr_x - x

def get_string_width(text):
    width = 0
    for char in text:
        if char == " ":
            width += 4
        else:
            coords = CUSTOM_FONT.get(char, [])
            w = max([px for px, py in coords], default=3) + 1 if coords else 4
            width += w + 1
    return max(0, width - 1)

# === STANJA I VARIJABLE ===
current_station_index = 0
last_api_update = 0
last_rotation_time = time.monotonic()
ROTATION_INTERVAL = 10 

tekstovi_za_prikaz = ["Učitavanje..."]
show_welcome = True
welcome_index = 0
WELCOME_DURATION = 5
last_welcome_switch = time.monotonic()

needs_redraw = True  # Fleg za kontrolu osvežavanja ekrana

WELCOME_MESSAGES = [
    ["Sistem spreman", "HTTP endpoint (URL) API", "uspešno povezan.", f"IP: {wifi.radio.ipv4_address}",f"Povezan na: {WIFI_SSID}"],
    ["Pokretanje softvera", "..............", "Proveravam internet", "konekciju..."],
    ["Pokretanje softvera", "...............................................", "Uspešno."],
    ["-"],
    ["Učitavaju se linije i", "stajališta. Sačekajte...", "Ukoliko potraje duže od", "30 sekundi molim vas da", "ponovo pokrenete uređaj."],
    ["Učitana stajališta:", "Kanarevo brdo (867,8)", "OŠ Đura Jakšić (939)", "Miljakovac /Pijaca/ (2365)", "Vareška (2910)"]
]

def build_display_lines(api_raw_data, station_uid):
    try:
        vehicles = api_raw_data

        if not isinstance(vehicles, list) or len(vehicles) == 0:
            station_name = STATIONS.get(str(station_uid), "Nedefinisano")
            return ["Trenutno nema dolazaka", "ili je došlo do greške. ", "Sačekajte 15 sekundi pre", "sledećeg ažuriranja.", f"ID stajališta: {station_uid} {station_name}"]

        valid_vehicles = [v for v in vehicles if v.get("seconds_left") is not None and int(v.get("seconds_left", 0)) > 0]

        if not valid_vehicles:
            station_name = STATIONS.get(str(station_uid), "Nedefinisano")
            return ["Trenutno nema dolazaka", "ili je došlo do greške. ", "Sačekajte 15 sekundi pre", "sledećeg ažuriranja.", f"ID stajališta: {station_uid} {station_name}"]

        valid_vehicles.sort(key=lambda v: int(v.get("seconds_left", 99999)))

        zamene = {
            "/Železnička stanica/": "/Žel. st./",
            "/Železnička Stanica/": "/Žel. st./",
            "Novi Beograd": "N. Bgd",
            "Ekspres": "eks.",
            "Železnička stanica": "ŽS",
            "Edvarda": "Edv.",
            "Stepanović": "St.",
        }

        lines = []
        for v in valid_vehicles[:5]:
            line = str(v.get("line_number", "??"))
            dest = v.get("main_line_title", "").split("-")[-1].strip()

            if not dest:
                dest = v.get("to_price", "")

            for stara, nova in zamene.items():
                dest = dest.replace(stara, nova)

            seconds = int(v.get("seconds_left", 0))
            minutes = max(1, (seconds + 59) // 60 - 1)

            lines.append(f"{line} {dest} {minutes}min")

        return lines

    except Exception as e:
        print(f"Greška u obradi podataka: {e}")
        return ["Greška u obradi"]

import gc


def fetch_arrivals(station_uid):
    headers = ANNOUNCEMENT_HEADERS.copy()
    headers["Referer"] = (
        f"https://online.bgnaplata.rs/sr/announcement_arrival/{station_uid}"
    )

    data = ANNOUNCEMENT_BASE_DATA.copy()
    data["r"] = str(station_uid)

    response = None

    try:
        gc.collect()
        print("RAM pre API:", gc.mem_free())

        response = requests.post(
            ANNOUNCEMENT_URL,
            headers=headers,
            data=data,
            stream=True
        )

        print("API odgovor primljen.")
        print("RAM posle HTTP:", gc.mem_free())

        json_data = json_stream.load(
            response.iter_content(32)
        )

        best_vehicles = []

        for vehicle in json_data:
            try:
                # Čitamo samo ono što nam treba.
                try:
                    seconds = vehicle["seconds_left"]
                except:
                    continue

                if seconds is None:
                    continue

                seconds = int(seconds)

                if seconds <= 0:
                    continue

                try:
                    line_number = vehicle["line_number"]
                except:
                    line_number = "??"

                try:
                    main_line_title = vehicle["main_line_title"]
                except:
                    main_line_title = ""

                try:
                    to_price = vehicle["to_price"]
                except:
                    to_price = ""

                small = {
                    "line_number": line_number,
                    "main_line_title": main_line_title,
                    "to_price": to_price,
                    "seconds_left": seconds
                }

                best_vehicles.append(small)

                best_vehicles.sort(
                    key=lambda x: x["seconds_left"]
                )

                # Čuvamo samo 5 najbližih.
                if len(best_vehicles) > 5:
                    best_vehicles.pop()

            except Exception as e:
                print("Preskočen objekat:", repr(e))

        print("API obrada završena.")
        print("Vozila:", len(best_vehicles))
        print("RAM pre close:", gc.mem_free())

        return best_vehicles

    except Exception as e:
        print("GREŠKA API:", repr(e))
        return []

    finally:
        if response is not None:
            try:
                response.close()
            except:
                pass

        gc.collect()
        print("RAM nakon GC:", gc.mem_free())
def render_lines(lines):
    # Čišćenje ekrana
    screen_bitmap.fill(0)

    for i in range(min(5, len(lines))):
        text = lines[i]
        y_pos = 1 + (i * LINE_HEIGHT)

        if not show_welcome and "min" in text:
            parts = text.rsplit(" ", 1)
            glavni, vreme = parts[0], parts[1]

            w_vreme = get_string_width(vreme)
            x_vreme = 128 - w_vreme
            draw_custom_string(screen_bitmap, vreme, x_vreme, y_pos)

            max_glavni_width = 128 - w_vreme - 4
            while len(glavni) > 0 and get_string_width(glavni + "...") > max_glavni_width:
                glavni = glavni[:-1]

            if len(glavni) < len(parts[0]):
                glavni = glavni + "..."

            draw_custom_string(screen_bitmap, glavni, 0, y_pos)
        else:
            draw_custom_string(screen_bitmap, text, 0, y_pos)

# === GLAVNA PETLJA ===
while True:
    now = time.monotonic()

    # Automatski reconnect ako Wi-Fi pukne
    if not wifi.radio.ipv4_address:
        connect_wifi()

    # Kontrola dobrodošlice
    if show_welcome:
        if now - last_welcome_switch >= WELCOME_DURATION:
            welcome_index += 1
            last_welcome_switch = now
            needs_redraw = True
            
            if welcome_index >= len(WELCOME_MESSAGES):
                show_welcome = False
                last_api_update = 0

    # API Poziv
    if not show_welcome and (now - last_api_update > 20 or last_api_update == 0):
        station_uid = STATION_IDS[current_station_index]
        try:
            decoded_res = fetch_arrivals(station_uid)
            tekstovi_za_prikaz = build_display_lines(decoded_res, station_uid)
            last_api_update = now
            needs_redraw = True

        except Exception as e:
            print(f"Greška prilikom API poziva: {e}")

            dani = ["Ponedeljak", "Utorak", "Sreda", "Četvrtak", "Petak", "Subota", "Nedelja"]
            meseci = ["januar", "februar", "mart", "april", "maj", "jun", "jul", "avgust", "sept", "oktobar", "novembar", "decembar"]

            t = time.localtime()
            datum = f"{dani[t.tm_wday]}, {t.tm_mday}. {meseci[t.tm_mon - 1]} {t.tm_year}."
            vreme_str = f"{t.tm_hour:02d}:{t.tm_min:02d}"

            tekstovi_za_prikaz = [
                "Greška servera",
                f"ID: {station_uid}",
                "",
                datum,
                f"Vreme: {vreme_str}",
            ]
            last_api_update = now
            needs_redraw = True

    # Rotacija stajališta
    if not show_welcome and now - last_rotation_time > ROTATION_INTERVAL:
        current_station_index = (current_station_index + 1) % len(STATION_IDS)
        last_rotation_time = now
        last_api_update = 0
        needs_redraw = True

    # Osvežavanje prikaza na ekranu – samo kada je aktiviran flag
    if needs_redraw:
        lines_to_draw = WELCOME_MESSAGES[welcome_index] if show_welcome else tekstovi_za_prikaz
        render_lines(lines_to_draw)
        needs_redraw = False

    time.sleep(0.05)
