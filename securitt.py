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
        self.modes = {'disarm': 'disarmed', 'disarmed': 'disarmed', 'arm_day_zones': 'armed_home', 'arm_night_zones': 'armed_night', 'arm_all_zones': 'armed_away'}
        self.ha_commands = {'disarm': 'disarm', 'arm_home': 'arm_day_zones', 'arm_night': 'arm_night_zones', 'arm_away': 'arm_all_zones'}
        self.button_commands = {'disarmed': 'disarmed', 'armed_home': 'arm_day_zones', 'armed_night': 'arm_night_zones', 'armed_away': 'arm_all_zones'}
        self.sensors = {}
        self.sensor_list = []
        self.keyfobs = {}
        self.keyfob_list = []
        self.buttons = {}
        self.button_list = []

    def load_config(self, reload=False):
        with open('/app/data/config.yaml', 'r') as config_file:
            config = yaml.safe_load(config_file)
            mqtt = config['mqtt']
            sensors = config['sensors']
            keyfobs = config['keyfobs']
            buttons = config['buttons']
            self.panel_settings = config['panel']
            self.codes = self.panel_settings['codes']
            self.keypads = config['keypads']
            self.sirens = config['sirens']
            self.z2m_topic = mqtt['z2m_topic']
            self.log_settings = config['logging']
            if 'notify' in config.keys():
                notify = config['notify']
                self.gotify_key = notify['gotify_key']
                self.phone_numbers = notify['phone_numbers']
        config_file.close()

        for sensor in sensors:
            name = sensor['name']
            type = sensor['type']
            instant = sensor['instant'] if 'instant' in sensor else False
            active = sensor['active']
            self.sensors[name] = {}
            self.sensors[name]['type'] = type
            self.sensors[name]['active'] = active
            self.sensors[name]['instant'] = instant
            self.sensor_list.append(name)

        for fob in keyfobs:
            fob_name = fob['name']
            fob_enabled = fob['enabled']
            fob_modes = fob['modes']
            self.keyfobs[fob_name] = {}
            self.keyfobs[fob_name]['enabled'] = fob_enabled
            self.keyfobs[fob_name]['modes'] = fob_modes
            self.keyfob_list.append(fob_name)

        for button in buttons:
            button_name = button['name']
            button_enabled = button['enabled']
            button_actions = button['actions']
            self.buttons[button_name] = {}
            self.buttons[button_name]['enabled'] = button_enabled
            self.buttons[button_name]['actions'] = button_actions
            self.button_list.append(button_name)

        if not reload:
            self.mqtt_host = mqtt['host'] if 'host' in mqtt else None
            self.mqtt_port = mqtt['port'] if 'port' in mqtt else 1883
            self.mqtt_user = mqtt['user'] if 'user' in mqtt else None
            self.mqtt_pass = mqtt['password'] if 'password' in mqtt else None
            self.mqtt_qos = mqtt['qos'] if 'qos' in mqtt else 1
            self.base_topic = mqtt['base_topic'] if 'base_topic' in mqtt else 'securitty'
            self.log_level = self.log_settings['log_level'].upper() if 'log_level' in self.log_settings else 'INFO'

    def keypad_input(self, action, device, code):
        """ Process input from alarm keypads and key fobs """
        if action in ['disarm', 'arm_day_zones', 'arm_night_zones', 'arm_all_zones']:
            self.set_mode(action, code, device)

    def button_input(self, action, device):
        self.set_mode(action, False, device)

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

    def set_mode(self, action, code, device=None):
        code_list = self.codes
        alarm_state = self.alarm_state
        disarmed = ['disarm', 'disarmed']

        if not code:
            # if action is carried out with a key fob or a button
            user = device
        else:
            user = code_list[int(code)]

        if action not in disarmed:
            self.exit_delay(action, device, user)

        elif action in disarmed:
            if alarm_state == 'triggered':
                # if alarm is disarmed after being triggered
                for siren in self.sirens:
                    client.publish(f'{self.z2m_topic}/{siren}/set', json.dumps({"warning": {"mode": "stop", "strobe": "false", "duration": 1}}))
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
                client.publish(f'{self.z2m_topic}/{pad}/set', json.dumps({'arm_mode': {'mode': action}}))

    def exit_delay(self, action, keypad, user):
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
            client.publish(f'{self.z2m_topic}/{keypad}/set', json.dumps({'arm_mode': {'mode': 'arming_away'}}))

        self.exit_delay_timer = timers[mode]
        exit_delay_timer = self.exit_delay_timer
        exit_delay_timer.start()

    def entry_delay(self, state):
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
        self.save_alarm_state()
        logger.info(f'Alarm state changed to {self.alarm_state} by {user}')

    def alarm_arm_away(self, user):
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'armed_away'
        for pad in self.keypads:
            client.publish(f'{self.z2m_topic}/{pad}/set', json.dumps({'arm_mode': {'mode': 'arm_all_zones'}}))
        self.alarm_mode_changed(user)

    def alarm_arm_home(self, user):
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'armed_home'
        self.alarm_mode_changed(user)

    def alarm_arm_night(self, user):
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'armed_night'
        self.alarm_mode_changed(user)

    def alarm_disarmed(self, user):
        self.prev_alarm_state = self.alarm_state
        self.alarm_state = 'disarmed'
        self.alarm_mode_changed(user)

    def alarm_triggered(self, state):
        logger.info(f'Alarm triggered by {self.trigger_sensor}!')

        self.alarm_state = 'triggered'
        self.save_alarm_state()

        siren_time = self.panel_settings[state]['alarm_time']
        self.alarm_timer = threading.Timer(siren_time, self.restore_state_after_triggered)
        alarm_timer = self.alarm_timer
        alarm_timer.start()

        for siren in self.sirens:
            client.publish(f'{self.z2m_topic}/{siren}/set', json.dumps({"warning": {"mode": "emergency", "strobe": "false", "duration": f'{siren_time}'}}))

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
        client.publish(f'{self.base_topic}/alarm_state', state, retain=True)

        try:
            with open('/app/data/.state', 'w') as file:
                file.write(json.dumps({'current_state': state, 'previous_state': prev_state}))
            file.close()
        except Exception as e:
            logger.error(f'Unable to write to state file: {e}')

def mqtt_connect():
    """Connect to MQTT broker and set LWT"""
    try:
        client.username_pw_set(a.mqtt_user, a.mqtt_pass)
        client.will_set(f'{a.base_topic}/status', 'offline', 1, True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(a.mqtt_host, a.mqtt_port)
        client.publish(f'{a.base_topic}/status', 'online', 1, True)
    except Exception as e:
        logger.error(f'Unable to connect to MQTT broker: {e}')
        sys.exit(1)

def on_connect(client, userdata, flags, rc):
    # The callback for when the client receives a CONNACK response from the MQTT broker.
    logger.info('Connected to MQTT broker with result code ' + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.

    # topic for receiving commands from external sources
    client.subscribe(f'{a.base_topic}/set_mode')
    client.subscribe(f'{a.base_topic}/reload_config')

    # topics for receiving data from devices
    for sensor in a.sensor_list:
        client.subscribe(f'{a.z2m_topic}/{sensor}')
    for keypad in a.keypads:
        client.subscribe(f'{a.z2m_topic}/{keypad}')
    for fob in a.keyfob_list:
        client.subscribe(f'{a.z2m_topic}/{fob}')
    for button in a.button_list:
        client.subscribe(f'{a.z2m_topic}/{button}')

def on_message(client, userdata, msg):
    topic = str(msg.topic)
    ignored_actions = [None, 'null', '']
    if a.z2m_topic in topic:
        payload = json.loads(msg.payload.decode('utf-8'))
        device = topic.replace(f'{a.z2m_topic}/','')
    
        # if monitored sensor changes state
        if device in a.sensor_list: 
            a.sensor_state_change(device, payload)

        # if an action is carried out on a keypad
        elif device in a.keypads:
            action = payload['action']
            code = payload['action_code']
            valid_codes = a.codes
            if code != None:
                if int(code) in valid_codes.keys():
                    a.keypad_input(action, device, code)
                    logger.debug(f"Received command from keypad '{device}': {action}")

        # if an action is carried out with a key fob
        elif device in a.keyfob_list:
            enabled = a.keyfobs[device]['enabled']
            action = payload['action']
            if action not in ignored_actions:
                if enabled:
                    allowed_mode = a.modes[action] in a.keyfobs[device]['modes']
                    if allowed_mode:
                        a.keypad_input(action, device, False)
                    else:
                        logger.warning(f"Received command '{action}' from device '{device}', but {action} is not enabled for {device}")
                else:
                    logger.warning(f"Received command '{action}' from device '{device}', but {device} is not enabled")

        # if an action is carried out with a button
        elif device in a.button_list:
            enabled = a.buttons[device]['enabled']
            action = payload['action']
            single = ['on', 'off', 'single']
            double = ['double']
            if action not in ignored_actions:
                if enabled:
                    allowed_actions = action in a.buttons[device]['actions'].keys()
                    if allowed_actions:
                        if action in single:
                            button_action = a.buttons[device]['actions']['single']
                        elif action in double:
                            button_action = a.buttons[device]['actions']['double']
                        alarm_action = a.button_commands[button_action]
                        a.button_input(alarm_action, device)
                    else:
                        logger.warning(f"Received command '{action}' from device '{device}', but {action} is not enabled for {device}")
                else:
                    logger.warning(f"Received command '{action}' from device '{device}', but {device} is not enabled")

    # receive command from Home Assistant
    elif 'set_mode' in topic:
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

    elif 'reload_config' in topic:
        try:
            a.load_config(reload=True)
            logger.info('Configuration reloaded')
        except Exception as e:
            logger.error(f'Unable to reload configuration: {e}')

if __name__ == '__main__':

    a = Alarm()
    a.load_config()

    logger = logging.getLogger('log')
    log_days = a.log_settings['retain_days']
    handler = TimedRotatingFileHandler('/app/data/securitt.log', when="midnight", backupCount=log_days)

    if a.log_level.lower() not in ['debug', 'info', 'warning', 'error']:
        logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s: %(message)s', handlers=(handler,))
    else:
        logging.basicConfig(level=a.log_level, format='%(asctime)s %(levelname)s: %(message)s', handlers=(handler,))

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

    client = mqtt_client.Client(a.base_topic)

    mqtt_connect()
    client.loop_forever()