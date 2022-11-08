import os
import sys
import yaml
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import threading
import paho.mqtt.client as mqtt_client

class Alarm:
    def __init__(self):
        self.modes = {'disarm': 'disarmed', 'arm_day_zones': 'armed_home', 'arm_night_zones': 'armed_night', 'arm_all_zones': 'armed_away'}
        self.ha_commands = {'disarm': 'disarm', 'arm_home': 'arm_day_zones', 'arm_night': 'arm_night_zones', 'arm_away': 'arm_all_zones'}

    def keypad_input(self, action, keypad, code):
        """ Process input from alarm keypads """
        if action in ['disarm', 'arm_day_zones', 'arm_night_zones', 'arm_all_zones']:
            self.set_mode(action, code, keypad)

    def sensor_state_change(self, sensor, payload):
        """ Process monitored sensor state changes """
        sensor_type = self.sensors[sensor]['type']
        alarm_state = self.alarm_state

        if sensor_type == 'contact':
            state = payload['contact']
            sensor_on = True if not state else False # contact attribute in payload is True for closed and False for open. We reverse that here.
            description = 'opened' if sensor_on else 'closed'

        elif sensor_type == 'motion':
            state = payload['occupancy']
            sensor_on = True if state else False # occupancy attribute in payload is True for motion detected and False for motion cleared
            description = 'detected' if sensor_on else 'cleared'

        if sensor_on and alarm_state != 'disarmed':
            self.check_if_sensor_active(sensor, description)

    def check_if_sensor_active(self, sensor, description):
        """ Check if sensor is active in current alarm mode """
        sensor_active = self.sensors[sensor]['active']
        instant = self.sensors[sensor]['instant']
        alarm_state = self.alarm_state
        active = False

        if alarm_state in ['armed_home', 'armed_night', 'armed_away']:
            if sensor_active == 'always' or alarm_state in sensor_active:
                active = True

        if active:
            self.trigger_sensor = sensor
            if instant:
                self.alarm_triggered(alarm_state)
            else:
                self.entry_delay(alarm_state)

    def set_mode(self, action, code, keypad=None):
        """ Change alarm mode """
        code_list = self.codes
        user = code_list[int(code)]
        alarm_state = self.alarm_state

        if action != 'disarm':
            self.exit_delay(action, keypad, user)

        elif action == 'disarm':
            if alarm_state == 'triggered':
                # if alarm is disarmed after being triggered
                for siren in self.sirens:
                    client.publish(f'{z2m_topic}/{siren}/set', json.dumps({"warning": {"mode": "stop", "strobe": "false", "duration": 1}}))
                alarm_timer = self.alarm_timer
                alarm_timer.cancel()

            elif alarm_state == 'arming': 
                # stop the exit delay if the alarm is disarmed during the exit delay
                exit_delay_timer = self.exit_delay_timer
                exit_delay_timer.cancel()
                logger.info(f'Exit delay canceled by {user}')

            elif alarm_state == 'pending': 
                # stop the entry delay if the alarm is disarmed during the entry delay
                entry_delay_timer = self.entry_delay_timer
                entry_delay_timer.cancel()
                logger.info(f'Entry delay canceled by {user}')

            self.alarm_disarmed(user)

        if action != 'arm_all_zones':
            for pad in self.keypads:
                # change keypad LEDs on alarm mode change
                client.publish(f'{z2m_topic}/{pad}/set', json.dumps({'arm_mode': {'mode': action}}))

    def exit_delay(self, action, keypad, user):
        """ Start the appropriate exit delay when alarm is armed """
        logger.info(f'Exit delay started by {user}')
        mode = self.modes[action]
        exit_delay = self.panel_settings[mode]['exit_delay']
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'arming'
        self.save_alarm_state()

        timers = {
            'armed_away': threading.Timer(exit_delay, self.alarm_arm_away, args=(user,)),
            'armed_home': threading.Timer(exit_delay, self.alarm_arm_home, args=(user,)),
            'armed_night': threading.Timer(exit_delay, self.alarm_arm_night, args=(user,))
        }

        if keypad != None and action == 'arm_all_zones':
            client.publish(f'{z2m_topic}/{keypad}/set', json.dumps({'arm_mode': {'mode': 'arming_away'}}))

        self.exit_delay_timer = timers[mode]
        exit_delay_timer = self.exit_delay_timer
        exit_delay_timer.start()

    def entry_delay(self, state):
        """ Start the appropriate entry delay when a monitored sensor is tripped """
        logger.debug(f'{self.trigger_sensor} tripped')

        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'pending'
        entry_delay = self.panel_settings[state]['entry_delay']
        self.save_alarm_state()

        self.entry_delay_timer = threading.Timer(entry_delay, self.alarm_triggered, args=(state,))
        entry_delay_timer = self.entry_delay_timer
        entry_delay_timer.start()

        logger.info('Entry delay started')

    def alarm_mode_changed(self, user):
        """ Save current and previous alarm mode to file """
        self.save_alarm_state()
        logger.info(f'Alarm state changed to {self.alarm_state} by {user}')

    def alarm_arm_away(self, user):
        """ Arm alarm in away mode """
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'armed_away'
        for pad in self.keypads:
            client.publish(f'{z2m_topic}/{pad}/set', json.dumps({'arm_mode': {'mode': 'arm_all_zones'}}))
        self.alarm_mode_changed(user)

    def alarm_arm_home(self, user):
        """ Arm alarm in home mode """
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'armed_home'
        self.alarm_mode_changed(user)

    def alarm_arm_night(self, user):
        """ Arm alarm in night mode """
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'armed_night'
        self.alarm_mode_changed(user)

    def alarm_disarmed(self, user):
        """ Disarm alarm """
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'disarmed'
        self.alarm_mode_changed(user)

    def alarm_triggered(self, state):
        """ Sound the alarm(s) when the alarm is triggered """
        logger.info(f'Alarm triggered by {self.trigger_sensor}!')

        self.alarm_state = 'triggered'
        self.save_alarm_state()

        siren_time = self.panel_settings[state]['alarm_time']
        self.alarm_timer = threading.Timer(siren_time, self.restore_state_after_triggered)
        alarm_timer = self.alarm_timer
        alarm_timer.start()

        for siren in self.sirens:
            client.publish(f'{z2m_topic}/{siren}/set', json.dumps({"warning": {"mode": "emergency", "strobe": "false", "duration": f'{siren_time}'}}))

    def restore_state_after_triggered(self):
        """ Return alarm to state before alarm was triggered """
        self.alarm_state = self.prev_alarm_state
        self.prev_alarm_state = 'triggered'
        logger.info(f'Alarm mode restored to {self.alarm_state}')
        self.save_alarm_state()

    def save_alarm_state(self):
        """ Publish alarm state to MQTT and save state to file """
        state = self.alarm_state
        prev_state = self.prev_alarm_state
        client.publish(f'{BASE_TOPIC}/alarm_state', state, retain=True)

        try:
            with open('/app/data/.state', 'w') as file:
                file.write(json.dumps({'current_state': state, 'previous_state': prev_state}))
            file.close()
        except Exception as e:
            logger.error(f'Unable to write to state file: {e}')

def mqtt_connect():
    """Connect to MQTT broker and set LWT"""
    try:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        client.will_set(f'{BASE_TOPIC}/status', 'offline', 1, True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.publish(f'{BASE_TOPIC}/status', 'online', 1, True)
    except Exception as e:
        logger.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit(1)

def on_connect(client, userdata, flags, rc):
    # The callback for when the client receives a CONNACK response from the MQTT broker.
    logger.info('Connected to MQTT broker with result code ' + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.

    # topic for receiving commands from Home Assistant
    client.subscribe(f'{BASE_TOPIC}/set_mode')

    # topics for receiving data from keypads and monitored sensors
    for sensor in sensor_list:
        client.subscribe(f'{z2m_topic}/{sensor}')
    for keypad in a.keypads:
        client.subscribe(f'{z2m_topic}/{keypad}')

def on_message(client, userdata, msg):
    topic = str(msg.topic)
    if z2m_topic in topic:
        payload = json.loads(msg.payload.decode('utf-8'))
        device = topic.replace(f'{z2m_topic}/','')
    
        # if monitored sensor changes state
        if device in sensor_list: 
            a.sensor_state_change(device, payload)
        
        # if an action is carried out on the keypad
        elif device in a.keypads:
            keypad = device
            action = payload['action']
            code = payload['action_code']
            valid_codes = a.codes
            if code != None:
                if int(code) in valid_codes.keys():
                    a.keypad_input(action, keypad, code)
                    logger.debug(f"Received command from keypad '{keypad}': {action}")

    # receive command from Home Assistant
    elif 'set_mode':
        payload = json.loads(msg.payload.decode('utf-8'))
        action = payload['action']
        code = payload['code']
        valid_codes = a.codes
        ha_commands = a.ha_commands
        if code != None:
            if int(code) in valid_codes.keys():
                if action in ha_commands.keys():
                    logger.debug(f'Received external command: {action}')
                    ha_action = ha_commands[action]
                    a.set_mode(ha_action, code)
                else:
                    logger.warning(f'Received invalid external command: {action}')

if __name__ == '__main__':

    a = Alarm()

    with open('/app/data/config.yaml', 'r') as config_file:
        config = yaml.safe_load(config_file)
        mqtt = config['mqtt']
        a.panel_settings = config['panel']
        a.codes = a.panel_settings['codes']
        sensors = config['sensors']
        a.keypads = config['keypads']
        a.sirens = config['sirens']
        z2m_topic = mqtt['z2m_topic']
        log_settings = config['logging']
        gotify_key = config['notify']['gotify_key']

    MQTT_HOST = mqtt['host'] if 'host' in mqtt else None
    MQTT_PORT = mqtt['port'] if 'port' in mqtt else 1883
    MQTT_USER = mqtt['user'] if 'user' in mqtt else None
    MQTT_PASSWORD = mqtt['password'] if 'password' in mqtt else None
    MQTT_QOS = mqtt['qos'] if 'qos' in mqtt else 1
    BASE_TOPIC = mqtt['base_topic'] if 'base_topic' in mqtt else 'securitt'
    LOG_LEVEL = log_settings['log_level'].upper() if 'log_level' in log_settings else 'INFO'

    logger = logging.getLogger('log')
    log_days = log_settings['retain_days']
    handler = TimedRotatingFileHandler('/app/data/securitt.log', when="midnight", backupCount=log_days)

    if LOG_LEVEL.lower() not in ['debug', 'info', 'warning', 'error']:
        logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s: %(message)s', handlers=(handler,))
    else:
        logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(levelname)s: %(message)s', handlers=(handler,))

    logger.info('===== Starting securitt =====')

    if not os.path.exists('/app/data/.state'):
        with open('/app/data/.state', 'x') as state_file:
            logging.info('No state file found; creating default state file.')
            state_file.write(json.dumps({'current_state': 'disarmed', 'previous_state': 'disarmed'}))
        state_file.close()

    with open('/app/data/.state', 'r') as state_file:
        states = state_file.readline()
    state_file.close()

    states = json.loads(states)
    a.alarm_state = states['current_state']
    a.prev_alarm_state = states['previous_state']

    logging.debug(f'Current alarm state: {a.alarm_state}')
    logging.debug(f'Previous alarm state: {a.prev_alarm_state}')

    a.sensors = {}
    sensor_list = []

    for sensor in sensors:
        name = sensor['name']
        type = sensor['type']
        instant = sensor['instant'] if 'instant' in sensor else False
        active = sensor['active']
        a.sensors[name] = {}
        a.sensors[name]['type'] = type
        a.sensors[name]['active'] = active
        a.sensors[name]['instant'] = instant
        sensor_list.append(name)

    client = mqtt_client.Client(BASE_TOPIC)

    mqtt_connect()
    client.loop_forever()