# Як підняти EC2 в us-east-1 і все підключити (покроково, з нуля)

Це інструкція «натисни сюди → введи це». Поки що ми **тільки готуємо сервер і
ганяємо paper** — реальних грошей тут нема.

Орієнтовна вартість: для підготовки й paper вистачить дешевого інстанса (~$15/міс або
free-tier). Інстанс для реальної низької латентності (`c6i.large`) ~$60/міс — його
вмикаєш ПІЗНІШЕ, коли дійде до live.

---

## Частина A. Завести акаунт AWS
1. Відкрий **https://aws.amazon.com** → **Create an AWS Account** (вгорі справа).
2. Email, пароль, ім'я акаунта.
3. Доведеться ввести **картку** (AWS бере ~$1 на перевірку й повертає) і підтвердити
   телефон.
4. Обрати план підтримки → **Basic (Free)**.
5. Зайти в консоль: **https://console.aws.amazon.com**

## Частина B. Обрати регіон us-east-1
1. У консолі вгорі **справа** — випадайка з регіоном.
2. Обери **US East (N. Virginia) — us-east-1**. (Дуже важливо: саме цей регіон.)

## Частина C. Запустити EC2-інстанс
1. У пошуку консолі введи **EC2** → відкрий сервіс EC2.
2. Натисни **Launch instance** (помаранчева кнопка).
3. **Name:** `poly-quoter`.
4. **Application and OS Images:** обери **Ubuntu** → **Ubuntu Server 24.04 LTS**
   (64-bit x86).
5. **Instance type:**
   - для початку/paper: **t3.small** (дешево),
   - для реальної латентності пізніше: **c6i.large**.
6. **Key pair (login):** натисни **Create new key pair**:
   - назва: `poly-key`, тип **RSA**, формат **.pem**,
   - натисни **Create** — браузер завантажить файл **`<ssh-key>.pem`**. **Збережи
     його, без нього не зайдеш.**
7. **Network settings** → **Edit**:
   - **Allow SSH traffic from** → обери **My IP** (лише з твоєї адреси — безпечно).
   - (HTTP/HTTPS НЕ відкривай — дашборд дивитимемось через тунель.)
8. **Configure storage:** постав **20 GiB** (gp3).
9. Натисни **Launch instance**.
10. **Instances** → дочекайся статусу **Running**, скопіюй **Public IPv4 address**
    (напр. `52.x.x.x`).

## Частина D. Підключитися з Mac (термінал)
У локальному терміналі (де лежить код):
```bash
cd ~/Downloads          # туди, куди завантажився ключ
chmod 400 <ssh-key>.pem  # права на ключ (обов'язково)
ssh -i <ssh-key>.pem ubuntu@ВСТАВ_PUBLIC_IP
```
Перший раз спитає `Are you sure...` → введи **yes**. Ти на сервері.

## Частина E. Встановити оточення на сервері
Уже у сесії `ubuntu@...`:
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3.11 python3.11-venv python3-pip git chrony
sudo systemctl enable --now chrony   # точний час
```

## Частина F. Залити код на сервер
**Варіант 1 — через scp (найпростіше, без GitHub).** У **локальному** терміналі
(новий таб, не на сервері):
```bash
cd ~/Downloads/TG_BOTS
scp -i ~/Downloads/<ssh-key>.pem -r poly-quoter ubuntu@ВСТАВ_PUBLIC_IP:~/poly-quoter
```
(Перед цим краще прибрати важке: `state.db*.bak` і `quoter/backtest/data/` не
обов'язкові — вони й так у .gitignore.)

**Варіант 2 — через git**, якщо колись заведеш приватний репозиторій.

## Частина G. Налаштувати .env і встановити залежності
Назад у сесії на сервері:
```bash
cd ~/poly-quoter
python3.11 -m venv .venv
.venv/bin/pip install -e .          # або: .venv/bin/pip install -r requirements.txt
```
Створи `.env` (значення ключів — лише тут, нікому не показуй):
```bash
nano .env
```
Встав (для paper достатньо MODE/BANKROLL; ключі POLY_* потрібні лише для live):
```
MODE=paper
BANKROLL=100
LOG_LEVEL=INFO
```
Збережи: `Ctrl+O`, `Enter`, `Ctrl+X`.

## Частина H. Зміряти латентність (заради чого все)
```bash
for i in $(seq 20); do curl -o /dev/null -s -w "%{time_connect} %{time_total}\n" https://clob.polymarket.com/time; done
```
Запиши медіану. **Ціль:** дуже малі числа (одиниці мс). Якщо великі — інша AZ/інстанс.

## Частина I. Запустити paper на сервері
```bash
cd ~/poly-quoter
nohup .venv/bin/python -m quoter.main > logs/server_paper.log 2>&1 &
```
Подивитись логи:
```bash
tail -f logs/server_paper.log
```

## Частина J. Подивитись дашборд (через тунель, безпечно)
У **локальному** терміналі:
```bash
ssh -i ~/Downloads/<ssh-key>.pem -L 8080:localhost:8080 ubuntu@ВСТАВ_PUBLIC_IP
```
Потім у браузері: **http://localhost:8080**

---

## Важливо про гроші
- Поки інстанс **Running** — він тарифікується щогодини. Коли не треба:
  **EC2 → Instances → Instance state → Stop** (зупинений інстанс майже безкоштовний,
  лише диск).
- **Terminate** = видалити назавжди.
- Постав **Billing alerts** (Billing → Budgets) на $10–20, щоб не було сюрпризів.

## Що НЕ робимо
Це лише paper на сервері (Частина I) + вимір латентності (H). **Live-режим (реальні
гроші) НЕ вмикаємо** — для нього окремо треба збудувати live-виконання + on-chain
merge (див. `docs/deploy-us-east-1-checklist.uk.md`, §7).
