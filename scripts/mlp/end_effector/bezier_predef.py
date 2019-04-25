import mlp.config as cfg
import time
import os
from mlp.utils.polyBezier import *
import pinocchio as pin
from pinocchio import SE3,Quaternion
from pinocchio.utils import *
import numpy.linalg
from multicontact_api import WrenchCone,SOC6,ContactSequenceHumanoid
import numpy as np
from tools.disp_bezier import *
import hpp_spline
from hpp_spline import bezier
import hpp_bezier_com_traj as bezier_com
from mlp.utils import trajectories
from mlp.utils.util import stdVecToMatrix
import math

def effectorCanRetry():
    return False


class Empty:
    None
    

def computeConstantsWithDDJerk(ddjerk,t):
    a = (1./6.)*ddjerk *t*t*t
    v = (1./24.) * ddjerk *t*t*t*t
    p = (1./120.) * ddjerk *t*t*t*t*t   
    return p,v,a

def computePosOffset(t_predef,t_total):
    timeMid= (t_total - (2.*t_predef))/2.
    p = cfg.p_max / (1. + 4.*timeMid/t_predef + 6.*timeMid*timeMid/(t_predef*t_predef) - (timeMid*timeMid*timeMid)/(t_predef*t_predef*t_predef))
    return p,0.,0.

def computePredefConstants(t):
    #return computeConstantsWithDDJerk(250.,cfg.EFF_T_PREDEF)
    return computePosOffset(cfg.EFF_T_PREDEF,t)

def buildPredefinedInitTraj(placement,t_total):
    p_off,v_off,a_off = computePredefConstants(t_total)
    normal = placement.rotation * np.matrix([0,0,1]).T
    c0 = placement.translation.copy()
    c1 = placement.translation.copy()
    c1 += p_off * normal
    dc0 = np.matrix(np.zeros(3)).T
    #dc1 = v_off * normal
    ddc0 = np.matrix(np.zeros(3)).T
    #ddc1 = a_off * normal
    #create wp : 
    n = 4.
    wps = np.matrix(np.zeros(([3,int(n+1)])))
    T = cfg.EFF_T_PREDEF
    # constrained init pos and final pos. Init vel, acc and jerk = 0
    wps[:,0] = (c0); # c0
    wps[:,1] =((dc0 * T / n )+  c0); #dc0
    wps[:,2] =((n*n*c0 - n*c0 + 2.*n*dc0*T - 2.*dc0*T + ddc0*T*T)/(n*(n - 1.)));#ddc0 // * T because derivation make a T appear
    wps[:,3] =((n*n*c0 - n*c0 + 3.*n*dc0*T - 3.*dc0*T + 3.*ddc0*T*T)/(n*(n - 1.))); #j0 = 0 
    wps[:,4] =(c1); #c1 
    return bezier(wps,T)

def buildPredefinedFinalTraj(placement,t_total):
    p_off,v_off,a_off = computePredefConstants(t_total)
    normal = placement.rotation * np.matrix([0,0,1]).T
    c0 = placement.translation.copy()
    c1 = placement.translation.copy()
    c0 += p_off * normal
    dc1 = np.matrix(np.zeros(3)).T
    #dc0 = v_off * normal
    ddc1 = np.matrix(np.zeros(3)).T
    #ddc0 = a_off * normal
    #create wp : 
    n = 4.
    wps = np.matrix(np.zeros(([3,int(n+1)])))
    T = cfg.EFF_T_PREDEF
    # constrained init pos and final pos. final vel, acc and jerk = 0
    wps[:,0] = (c0); #c0
    wps[:,1] = ((n*n*c1 - n*c1 - 3*n*dc1*T + 3*dc1*T + 3*ddc1*T*T)/(n*(n - 1))) ; # j1
    wps[:,2] = ((n*n*c1 - n*c1 - 2*n*dc1*T + 2*dc1*T + ddc1*T*T)/(n*(n - 1))) ; #ddc1 * T ??
    wps[:,3] = ((-dc1 * T / n) + c1); #dc1
    wps[:,4] = (c1); #c1
    return bezier(wps,T)



def generatePredefLandingTakeoff(time_interval,placement_init,placement_end):
    t_total = time_interval[1]-time_interval[0] - 2*cfg.EFF_T_DELAY
    #print "Generate Bezier Traj :"
    #print "placement Init = ",placement_init
    #print "placement End  = ",placement_end
    #print "time interval  = ",time_interval
    # generate two curves for the takeoff/landing : 
    # generate a bezier curve for the middle part of the motion : 
    bezier_takeoff = buildPredefinedInitTraj(placement_init,t_total)
    bezier_landing = buildPredefinedFinalTraj(placement_end,t_total)
    t_middle =  (t_total - (2.*cfg.EFF_T_PREDEF))
    assert t_middle >= 0.1 and "Duration of swing phase too short for effector motion. Change the values of predef motion for effector or the duration of the contact phase. "
    curves = []
    # create polybezier with concatenation of the 3 (or 5) curves :    
    # create constant curve at the beginning and end for the delay : 
    if cfg.EFF_T_DELAY > 0 :
        bezier_init_zero=bezier(bezier_takeoff(0),cfg.EFF_T_DELAY)
        curves.append(bezier_init_zero)
    curves.append(bezier_takeoff)
    curves.append(bezier(np.matrix(np.zeros(3)).T,t_middle)) # placeholder only
    curves.append(bezier_landing)
    if cfg.EFF_T_DELAY > 0 :
        curves.append(bezier(bezier_landing(bezier_landing.max()),cfg.EFF_T_DELAY))    
    pBezier = PolyBezier(curves) 
    return pBezier
    
def generateSmoothBezierTraj(time_interval,placement_init,placement_end,numTry=None,q_t=None,phase_previous=None,phase=None,phase_next=None,fullBody=None,eeName=None,viewer=None):
    if numTry > 0 :
        raise ValueError("generateSmoothBezierTraj will always produce the same trajectory, cannot be called with numTry > 0 ")
    predef_curves = generatePredefLandingTakeoff(time_interval,placement_init,placement_end)
    bezier_takeoff = predef_curves.curves[predef_curves.idFirstNonZero()]
    bezier_landing = predef_curves.curves[predef_curves.idLastNonZero()]
    id_middle = int(math.floor(len(predef_curves.curves)/2.))    
    # update mid curve to minimize velocity along the curve:
    # set problem data for mid curve : 
    pData = bezier_com.ProblemData() 
    pData.c0_ = bezier_takeoff(bezier_takeoff.max())
    pData.dc0_ = bezier_takeoff.derivate(bezier_takeoff.max(),1)
    pData.ddc0_ = bezier_takeoff.derivate(bezier_takeoff.max(),2)
    pData.j0_ = bezier_takeoff.derivate(bezier_takeoff.max(),3)
    pData.c1_ = bezier_landing(0)
    pData.dc1_ = bezier_landing.derivate(0,1)
    pData.ddc1_ = bezier_landing.derivate(0,2)
    pData.j1_ = bezier_landing.derivate(0,3)    
    pData.constraints_.flag_ = bezier_com.ConstraintFlag.INIT_POS | bezier_com.ConstraintFlag.INIT_VEL | bezier_com.ConstraintFlag.INIT_ACC | bezier_com.ConstraintFlag.END_ACC | bezier_com.ConstraintFlag.END_VEL | bezier_com.ConstraintFlag.END_POS | bezier_com.ConstraintFlag.INIT_JERK | bezier_com.ConstraintFlag.END_JERK
    t_middle =  predef_curves.curves[id_middle].max()
    res = bezier_com.computeEndEffector(pData,t_middle)
    bezier_middle = res.c_of_t
    
    curves = predef_curves.curves[::]
    curves[id_middle] = bezier_middle
    pBezier = PolyBezier(curves)
    ref_traj = trajectories.BezierTrajectory(pBezier,placement_init,placement_end,time_interval)    
    return ref_traj


"""

placement_init = SE3.Identity()
placement_init.translation = np.matrix([0.1,0.3,0]).T
placement_end = SE3.Identity()
placement_end.translation = np.matrix([0.6,0.22,0]).T
placement_end.rotation = Quaternion(0.9800666,0.1986693,0, 0).matrix()
t_total = 1.2
time_interval = [1,1+t_total]

"""