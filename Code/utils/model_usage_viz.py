import sys, os
# Add the project root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import re
from collections import deque
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import threading
import queue

# Настройки
LOG_FILE = 'logs/model_usage.log'  # шлях до вашого файлу .log
MAX_POINTS = 100        # скільки останніх точок зберігати на графіку
SLEEP_INTERVAL = 0.1    # інтервал перевірки нового рядка в секундах

# Регулярний вираз для парсингу рядків логу
pattern = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*total_tokens: (\d+), prompt_tokens: (\d+), completion_tokens: (\d+)"
)

def tail(f):
    """
    Генератор, що читає файл в режимі реального часу, повертає нові рядки,
    як тільки вони з'являються.
    """
    f.seek(0, 2)  # Переміститися в кінець файлу
    while True:
        line = f.readline()
        if line:
            yield line
        else:
            time.sleep(SLEEP_INTERVAL)  # Wait briefly before checking again


def load_initial_data(file_path, n):
    """
    Завантажує перші n рядків з файлу логу або всі рядки, якщо n == -1.
    """
    times = deque(maxlen=MAX_POINTS)
    total = deque(maxlen=MAX_POINTS)
    prompt = deque(maxlen=MAX_POINTS)
    completion = deque(maxlen=MAX_POINTS)

    with open(file_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if n != -1 and i >= n:  # Якщо n == -1, завантажуємо всі рядки
                break
            m = pattern.match(line)
            if not m:
                continue

            # Парсимо час і значення токенів
            ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
            t_total = int(m.group(2))
            t_prompt = int(m.group(3))
            t_comp = int(m.group(4))

            # Додаємо до деків
            times.append(ts)
            total.append(t_total)
            prompt.append(t_prompt)
            completion.append(t_comp)

    return times, total, prompt, completion


def monitor_log(file_path, data_queue):
    """
    Моніторинг файлу логу в окремому потоці.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        # Пропускаємо вже завантажені рядки
        f.seek(0, 2)

        for line in tail(f):
            m = pattern.match(line)
            if not m:
                continue

            # Парсимо час і значення токенів
            ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
            t_total = int(m.group(2))
            t_prompt = int(m.group(3))
            t_comp = int(m.group(4))

            # Передаємо дані в чергу
            data_queue.put((ts, t_total, t_prompt, t_comp))


def main():
    # Завантажуємо перші n рядків або всі, якщо n == -1
    n = -1  # Кількість рядків для завантаження (-1 для завантаження всіх)
    times, total, prompt, completion = load_initial_data(LOG_FILE, n)

    # Ініціалізація графіка
    plt.ion()
    fig, ax = plt.subplots()
    ln_total, = ax.plot(times, total, label='Total Tokens')
    ln_prompt, = ax.plot(times, prompt, label='Prompt Tokens')
    ln_comp, = ax.plot(times, completion, label='Completion Tokens')
    ax.legend()
    ax.set_xlabel('Час')
    ax.set_ylabel('Кількість токенів')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    fig.autofmt_xdate()

    # Оновлюємо графік після завантаження початкових даних
    ln_total.set_data(times, total)
    ln_prompt.set_data(times, prompt)
    ln_comp.set_data(times, completion)
    ax.relim()
    ax.autoscale_view()
    fig.canvas.draw()
    plt.pause(0.1)  # Allow the GUI to process events

    # Черга для передачі даних між потоками
    data_queue = queue.Queue()

    # Запускаємо моніторинг логу в окремому потоці
    monitor_thread = threading.Thread(
        target=monitor_log,
        args=(LOG_FILE, data_queue),
        daemon=True
    )
    monitor_thread.start()

    # Головний цикл для оновлення графіка
    while True:
        try:
            # Отримуємо дані з черги
            while not data_queue.empty():
                ts, t_total, t_prompt, t_comp = data_queue.get()

                # Додаємо до деків
                times.append(ts)
                total.append(t_total)
                prompt.append(t_prompt)
                completion.append(t_comp)

                # Оновлюємо дані на графіку
                ln_total.set_data(times, total)
                ln_prompt.set_data(times, prompt)
                ln_comp.set_data(times, completion)
                ax.relim()
                ax.autoscale_view()

            # Малюємо графік
            fig.canvas.draw()
            plt.pause(0.1)  # Keep the GUI responsive

        except KeyboardInterrupt:
            print("Exiting...")
            break


if __name__ == '__main__':
    main()
