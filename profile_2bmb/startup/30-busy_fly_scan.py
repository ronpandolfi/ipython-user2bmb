print(__file__)

"""Aerotech Ensemble PSO scan"""

NUM_FLAT_FRAMES = 10
NUM_DARK_FRAMES = 10
NUM_TEST_FRAMES = 10
ROT_STAGE_FAST_SPEED = 50


class EnsemblePSOFlyDevice(TaxiFlyScanDevice):
    motor_pv_name = Component(EpicsSignalRO, "motorName")
    start = Component(EpicsSignal, "startPos")
    end = Component(EpicsSignal, "endPos")
    slew_speed = Component(EpicsSignal, "slewSpeed")

    # scan_delta: output a trigger pulse when motor moves this increment
    scan_delta = Component(EpicsSignal, "scanDelta")

    # advanced controls
    delta_time = Component(EpicsSignalRO, "deltaTime")
    # detector_setup_time = Component(EpicsSignal, "detSetupTime")
    # pulse_type = Component(EpicsSignal, "pulseType")

    # TODO: complete
    # https://github.com/prjemian/ipython_mintvm/blob/master/profile_bluesky/startup/notebooks/busy_fly_scan.ipynb
    scan_control = Component(EpicsSignal, "scanControl")


psofly = EnsemblePSOFlyDevice("2bmb:PSOFly:", name="psofly")


def motor_set_modulo(motor, modulo):
    if not 0 <= motor.position < modulo:
        yield from bps.mv(motor.set_use_switch, 1)
        yield from bps.mv(motor.user_setpoint, motor.position % modulo)
        yield from bps.mv(motor.set_use_switch, 0)


def _init_tomo_fly_(*, samInPos=0, start=0, stop=180, numProjPerSweep=1500, slewSpeed=10, accl=1):
    pso = psofly
    #samStage = tomo_stage.x
    rotStage = tomo_stage.rotary
    det = pg3_det
    shutter = B_shutter

    yield from bps.mv(
        det.cam.nd_attributes_file, "monaDetectorAttributes.xml",
        det.hdf1.num_capture, numProjPerSweep    # + darks & flats
    )
    yield from set_image_frame()

    yield from bps.stop(rotStage)
    yield from motor_set_modulo(rotStage, 360.0)
    
    yield from bps.mv(
        rotStage.velocity, ROT_STAGE_FAST_SPEED, 
        rotStage.acceleration, 3)
    yield from bps.mv(
        rotStage, 0, 
        samStage, samInPos)

    # TODO: anything from _plan_edgeSet() needed? (Feb 2018 setup: 50-plans.py)
    logging.debug("end of _init_tomo_fly_()")


def tomo_scan(*, start=0, stop=180, numProjPerSweep=1500, slewSpeed=10, accl=1, md=None):
    """
    standard tomography fly scan with BlueSky
    """
    _md = md or OrderedDict()
    _md["project"] = "mona"
    _md["APS_storage_ring_current,mA"] = aps_current.value
    _md["datetime_plan_started"] = str(datetime.now())

    pso = psofly
    det = pg3_det
    rotStage = tomo_stage.rotary
    shutter = B_shutter

    staged_device_list = []
    monitored_signals_list = [
        det.image.array_counter,
        rotStage.user_readback,
        ]

    def cleanup():
        for d in [det.image.array_counter, rotStage.user_readback]:
            try:
                yield from bps.unmonitor(d)
            except IllegalMessageSequence:
                pass
        yield from bps.abs_set(shutter, "close", group='shutter')
        yield from bps.mv(rotStage.velocity, ROT_STAGE_FAST_SPEED)
        yield from bps.mv(rotStage, 0.00)
        yield from bps.wait(group='shutter')

    @bpp.stage_decorator(staged_device_list)
    @bpp.run_decorator(md=_md)
    @bpp.finalize_decorator(cleanup)
    def _internal_tomo():
        yield from bps.monitor(rotStage.user_readback, name="rotation")
        yield from bps.monitor(det.image.array_counter, name="array_counter")

        # TODO: darks & flats

        # do not touch shutter during development
        yield from bps.abs_set(shutter, "open", group="shutter")

        yield from _init_tomo_fly_(
            start=start,
            stop=stop,
            numProjPerSweep=numProjPerSweep,
            slewSpeed=slewSpeed,
            accl=accl)

        # back off to the taxi point (back-off distance before fly start)
        logging.debug("before taxi")
        yield from bps.mv(
            pso.start, start,
            pso.end, stop,
            pso.scan_control, "Standard",
            pso.scan_delta, 1.0*(stop-start)/numProjPerSweep,
            pso.slew_speed, slewSpeed,
            rotStage.velocity, ROT_STAGE_FAST_SPEED,
            rotStage.acceleration, slewSpeed/accl
        )
        yield from bps.mv(pso.taxi, "Taxi")
        logging.debug("after taxi")

        # run the fly scan
        logging.debug("before fly")
        yield from bps.mv(rotStage.velocity, slewSpeed)
        yield from bps.wait(group='shutter')    # shutters are slooow, MUST be done now
        #yield from bps.trigger(det, group='fly')
        yield from bps.abs_set(det.cam.acquire, 1)
        yield from bps.abs_set(pso.fly, "Fly", group='fly')
        yield from bps.wait(group='fly')
        yield from bps.abs_set(det.cam.acquire, 0)
        logging.debug("after fly")
        # return rotStage to standard

        # read the camera
        #yield from bps.create(name='primary')
        #yield from bps.read(det)
        #yield from bps.save()

    return (yield from _internal_tomo())