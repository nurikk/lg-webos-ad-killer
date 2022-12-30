# Remove ads from LG WebOs home screen
Tested on OLED65C24LA
    
    fw: 03.21.30


**WARNING!** This script is provided with no warranty, use at your own risk. 
I have done the best I can to ensure the changes are safe and that they are not permanent.
To neutralize this script, simply remove the USB and reboot the TV.

## Installation
1. You must root your TV and have webosbrew installed
2. SSH to your TV

```
curl -o /var/lib/webosbrew/init.d/ad_killer https://raw.githubusercontent.com/nurikk/lg-webos-ad-killer/master/ad_killer
chmod +x /var/lib/webosbrew/init.d/ad_killer
```

After reboot your tv OR run command
```
/var/lib/webosbrew/init.d/ad_killer
```
