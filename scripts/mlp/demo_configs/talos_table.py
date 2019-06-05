TIMEOPT_CONFIG_FILE = "cfg_softConstraints_talos_rarm.yaml"
from common_talos import *
SCRIPT_PATH = "sandbox.dynamic"

DURATION_INIT = 1.5 # Time to init the motion
DURATION_FINAL = 2.9 # Time to stop the robot
DURATION_FINAL_SS = 1.
DURATION_SS =1.4
DURATION_DS = 2.9
DURATION_TS = 2.9


## Settings for end effectors : 
EFF_T_PREDEF = 0.3
p_max = 0.1