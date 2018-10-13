PanelDue for Klipper Install Guide
====

NOTE:
While it is possible to use a USB to serial adapter, this guide will assume you
are attaching your PanelDue directly to your Raspberry Pi's onboard UART

1. To enable the UART via your Pi's GPIO pins follow the guide here: 

https://spellfoundry.com/2016/05/29/configuring-gpio-serial-port-raspbian-jessie-including-pi-3/

While the guide goes more in depth, the summary is:
> sudo nano /boot/config.txt

and add the line (at the bottom):
enable_uart=1

Then disable any conflicting services:
> sudo systemctl disable serial-getty@ttyAMA0.service

> sudo systemctl disable serial-getty@ttyS0.service

> sudo nano /boot/cmdline.txt

remove the portion: console=serial0,115200 and save.

Now reboot
> sudo reboot

2. Connect your PanelDue to your Pi.

   Use the following image as a reference (the pins for rpi2 and 2pi3 are the same):
   
   https://docs.microsoft.com/en-us/windows/iot-core/media/pinmappingsrpi/rp2_pinout.png
   
   You are interested in pins:
   
| Pin # | Purpose |
|-------|---------|
| 4     | 5V      |
|  6    | GND     |
| 8     | TX      |
| 10    | RX      |
   
   !!!! IMPORTANT: Do not connect your PanelDue's communication pins to anything over than 3.3V
   or you can cause permanent damage. !!!!!!
   
   A genuine PanelDue uses the follow wire color coding:
   
| Color  | Pin |
|--------|-----|
| Red    | 5V  |
|  Black | GND |
| Blue   | TX  |
| Green  | RX  |
   
   However, please verify these are correct for your device. Clones will not be the same.
   
   Now make the following connections:
   
| Pi  | PD  |
|-----|-----|
| 5V  | 5V  |
| GND | GND |
| RX  | TX  |
| TX  | RX  |
	
   Note that the RX goes to TX and vice versa.
   Also note that the PanelDue may require a bit of current from your Pi.
   You may consider using a different 5V source for your PD if you have any concerns around 
   overloading your Pi's power supply.
	
3. Test your serial connection. (Optional step)

   At this point your PanelDue screen should be on but stuck in the "Connecting..." status
   You can use "tio" to test the connection.
   
   > sudo apt-get install tio
   
   follow the prompts and enter Y to install
   
   Your serial port path should be /dev/ttyS0 and the default baudrate is 57600.
   Run the following:
   
   > sudo tio --baudrate 57600 --databits 8 --flow none --stopbits 1 --parity none /dev/ttyS0
   
  	Now test that you can receive by looking for M408 commands. 
	You should see a new message pop up every 5 seconds or so.
	You can test sending to the PD by copy and pasting the following command:
	
    > {"status": "I", "myName":"Connection Test", "message":"Test Successful!" }
    
	Paste this into the console and hit enter. You will not get any feedback from tio,
	but if successful your PD should now be in the Idle state and should display
	a "Test Successful!" message box.
	
4. Configure klipper

   It is highly recommend that in addition to configure the paneldue extra you also configure the
   virtual SD extra. This allows for printing from file.
   
   A sample config looks like the following:
   
[virtual_sdcard]
path: ~/.octoprint/uploads

[paneldue]
serial_port: /dev/ttyS0
serial_baudrate: 57600
macro_list:
        NOZZLE_CLEAN
        QUAD_GANTRY_LEVEL

   Using your octoprint upload folder means that you can manage these files through octoprint
   (upload, delete, etc). It's not necessary but may be helpful.
   
   macro_list takes a list of macros you'd like to see in your macrolist. These must be
   commands that don't require parameters.
   
   Now save and restart the klipper service.
   
   > sudo service klipper restart
   
   You can monitor the log file to see if there are any problems that need fixing:
   
   > tail -f /tmp/klippy.log
   
   

