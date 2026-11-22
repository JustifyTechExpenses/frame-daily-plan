# Frame Daily Plan

**Project page:** https://github.com/outsidenwnc/frame-daily-plan

A calm morning planner for the **Samsung The Frame** TV. Each morning (by default 7:00 to 10:00) your Frame shows the family's day in Art Mode instead of a painting:

- today's and tomorrow's events,
- to-dos,
- the weather with a "what to wear" hint,
- a month calendar.

Afterwards it switches back to the artwork you had, and it deletes the plan image so nothing piles up.

![The plan on the Frame (demo data)](docs/plan-en.jpg)

It runs as a small Docker container on an always-on machine at home, such as a Synology NAS or a Raspberry Pi. You set everything up in a web page:

![The web page](docs/web-ui.png)

## Why

Art Mode is quiet, ad-free and always on the wall, so it's a good place for a family plan. Children who do best with a predictable day can see what is coming without a phone or a TV menu full of ads. The plan never interrupts a TV programme: if someone is watching, it waits until Art Mode comes back on.

## Features

- **Calendars**: any iCal feed, such as Google Calendar's secret address, Apple iCloud's public calendars or school calendars. Each calendar gets a colour. A calendar can also be a "to-do" calendar, where every entry goes on the to-do list.
- **Public holidays** for about 150 countries and their regions.
- **Weather** from [Open-Meteo](https://open-meteo.com/), with no API key.
- **Plan in English or German**, and a web page in English or German.
- **Plan on your artwork**: the plan sits on a blurred copy of the picture it replaces. You can also use a calm built-in gradient.
- **Upload your own picture**: it is fitted to 3840×2160 and kept on the TV as your normal artwork.
- **Show a message**: big text on a calm background, e.g. "Grandma picks you up at 4 pm today".

  ![Message](docs/message.jpg)

- **Apple TV remote → Art Mode**: when your Apple TV goes to sleep, the Frame goes to Art Mode instead of switching off, and it comes back to the TV picture when the Apple TV wakes up (see below).
- **Demo plan** with made-up data, for photos and videos.
- Past events turn grey. Long titles shrink to fit and are never cut off.

## Requirements

- **A Samsung The Frame** on your home network. It has been tested on a 2017 model (UE55LS003, Art API 1.07). Newer models should work, because the app falls back to the standard upload method, but they are untested. Please report your model (see Compatibility below).
- **An always-on machine that runs Docker**, on the same network as the TV: for example a Synology NAS with Container Manager, a Raspberry Pi, or any Linux server. Images are built for `amd64` and `arm64`.
- **Calendar links in iCal format.** Google Calendar: *Settings → your calendar → "Secret address in iCal format"*.
- No Samsung account, no SmartThings, no cloud service. Internet access is only needed for the calendars and the weather.

## Install

### Synology (Container Manager)

1. In **File Station**, create a folder `docker/frame-daily-plan`.
2. **Container Manager → Project → Create**. Name it `frame-daily-plan`, set the path to that folder and choose **"Create docker-compose.yml"**. Paste:

   ```yaml
   services:
     frame-daily-plan:
       image: ghcr.io/outsidenwnc/frame-daily-plan:latest
       container_name: frame-daily-plan
       restart: unless-stopped
       network_mode: host
       environment:
         TZ: Europe/London
       volumes:
         - ./data:/data
   ```

3. Finish the wizard. The container starts.
4. Open `http://<your-NAS>:8765` and follow **Getting started**.

### Any Docker host (Raspberry Pi, Linux server)

```bash
mkdir frame-daily-plan && cd frame-daily-plan
curl -O https://raw.githubusercontent.com/outsidenwnc/frame-daily-plan/main/docker-compose.yml
docker compose up -d
```

Then open `http://<host>:8765`.

### Getting started (in the web page)

1. **Search the network.** The app finds your Frame (or enter its IP address by hand).
2. **Connect to TV.** Click it, then press **Allow** on the TV within two minutes.
3. **Place for the weather.** Type your town and pick it. This also sets the time zone and the holiday country.
4. **Calendars.** Paste your iCal addresses, pick colours, and save.

Use **Create preview** to see the plan without touching the TV, and **Show plan now** to try it on the TV.

## Apple TV: Art Mode instead of off

With an Apple TV, the Apple TV remote switches the Frame off completely (over HDMI-CEC), so it never goes to Art Mode. Frame Daily Plan watches the Apple TV over the network and makes the Frame follow it:

- **Apple TV goes to sleep** → the Frame goes to Art Mode (and shows today's plan, if it's plan time).
- **Apple TV wakes up** → the Frame shows the TV picture.

### Set up

1. **Pair:** in the web page, card **Apple TV**, click **Search for Apple TV**, then **Pair**. A PIN appears on the TV; type it in within 30 seconds. The Frame must show the Apple TV picture while you pair.
2. **On the Apple TV**, set *Settings → General → Sleep After* to **Never**, so it only sleeps when you put it to sleep. The app can't tell which input the Frame is showing, so an Apple TV falling asleep by itself would switch the Frame away while someone watches another input.
3. **Choose one of two ways** for *Settings → Remotes and Devices → "Control TVs and Receivers"* on the Apple TV:

| | "Control TVs and Receivers" **on** (tick the box in the card) | "Control TVs and Receivers" **off** |
| --- | --- | --- |
| Apple TV wakes up | The Apple TV switches the Frame on **and to its own input** (HDMI-CEC), even if a games console was on last | The app leaves Art Mode; the input stays where it was |
| Apple TV goes to sleep | The Apple TV switches the Frame off; the app switches it back on into Art Mode with Wake-on-LAN (about a minute, network cable only) | The app sends the Frame to Art Mode after 3 seconds |
| Volume | over HDMI-CEC as usual | set *Volume Control* to **IR** |
| Best for | an Apple TV plus other devices, such as a games console | the Apple TV as the only device |

A 2017 Frame ignores direct input keys sent over the network, which is why the app itself can't switch back to the Apple TV input.

### Options

- **At night, off completely:** inside a time window such as 23:00–06:00, the Frame stays off when the Apple TV goes to sleep. At the daily plan's start time the app switches it on with Wake-on-LAN, straight into Art Mode, so the plan still appears.
- If the Frame was switched off completely (e.g. after a power cut), it is woken with Wake-on-LAN when the Apple TV wakes up. This needs the TV on a network cable, and the app must have seen the TV once to learn its MAC address.
- Tip: on the Frame, turn off *Settings → General → Smart Features → Autorun Smart Hub*, so it returns to the last input instead of the Smart Hub.

The Apple TV is watched with [pyatv](https://pyatv.dev/) over the local network (Companion protocol); the pairing is stored in `data/pyatv.json`. Tested with an Apple TV 4K (tvOS 27) and the 2017 Frame (UE55LS003). A 2017 Frame doesn't always report its Art Mode state while showing the TV picture, so no answer is taken as "TV picture".

## How it behaves

- At the start time, the app remembers the current artwork, uploads a fresh plan and shows it. If the TV is in TV mode, the plan just waits.
- At the end time, it switches back to that artwork and deletes the plan.
- Anything you show by hand *inside* that window disappears at its end. Anything you show *outside* it stays until **Back to artwork**.
- If the TV is asleep or unreachable, the app retries every 2 minutes during the time window, so the plan appears soon after the TV wakes up.
- Only images the app uploaded itself are ever deleted. Your photos and Samsung art are never touched.

## Security

The web page shows your private calendar links. Keep it inside your home network, and don't forward the port to the internet. To add a password, set `FRAME_PLAN_PASSWORD` in `docker-compose.yml`.

## Troubleshooting

- **"TV not reachable" or "Connect" times out.** Press **Allow** on the TV while the app is waiting. If you once pressed *Deny*, open *Settings → General → External Device Manager → Device Connection Manager → Device List* on the TV, and allow or delete "Frame Daily Plan".
- **"Search the network" finds nothing.** The container needs `network_mode: host`. Or enter the TV's IP address by hand.
- **Apple TV: "The PIN expired".** The Apple TV closes the PIN screen after about 30 seconds. Click **Pair** again and type the new PIN straight away.
- **Apple TV: the Frame stays dark after the Apple TV goes to sleep.** With "Control TVs and Receivers" on, the Frame comes back only through Wake-on-LAN: it needs a network cable, and the app must have reached the TV once while it was on. The log at the bottom of the page says what happened.
- **New calendar entries don't show up.** Google updates secret iCal feeds with a delay, sometimes up to a few hours.
- **The log** is at the bottom of the web page and in `data/frame-daily-plan.log`.

## Compatibility

| Model | Year | Art API | Status |
| --- | --- | --- | --- |
| UE55LS003 | 2017 | 1.07 | ✅ works |

Tried another Frame? Please open an issue with the model, year and whether it worked.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests
FRAME_PLAN_DATA=./data .venv/bin/python app/web.py   # http://localhost:8765
```

## Credits and licence

MIT, see `LICENSE`. Third-party parts and their licences are listed in `THIRD_PARTY_NOTICES.md`; the Samsung connection uses [samsungtvws](https://github.com/NickWaterton/samsung-tv-ws-api) (LGPL-3.0). Weather data is by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).

Not affiliated with or endorsed by Samsung. "Samsung", "The Frame" and "SmartThings" are trademarks of Samsung Electronics.
