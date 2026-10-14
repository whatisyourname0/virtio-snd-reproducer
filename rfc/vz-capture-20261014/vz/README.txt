Actual VZ microphone integration: prerequisites and acceptance recipe

This optional hardware lane is distinct from the source-extracted host tests
and synthetic QEMU lane. The kernel RFCs do not configure a host microphone
or add audio to Docker Desktop. A host launcher must create a virtio sound
input stream backed by the host default audio input and have microphone
permission. LinuxKit launcher work is tracked separately in PR #4228:
https://github.com/linuxkit/linuxkit/pull/4228
Do not interpret that PR link as a guarantee of merge state or as a pin of
the exact privately tested executable. This bundle contains kernel/QEMU
reproducers, not a rebuilt/signed macOS launcher or Industrial Base code.

Recorded snapshot3 environment
- Apple Silicon; current preparation host reports macOS 26.7 (25G229).
  This OS fact was captured during packaging, not in the original run log.
- Apple Virtualization.framework via the local LinuxKit launcher.
- Guest kernel: 7.3.0-rc6-linuxkit-vz-compose-r3-20261014.
- Historical launch flags: --cpus 2 --mem 4096 --networking vmnet, 8G disk.
- Guest initrd SHA256:
  d04d75e079b59a7f7481154bb1e03541f7a416bc61aa0987d5250004df674f61
- Guest LinuxKit kernel config SHA256:
  0fce7ead449973dd39a7b7fdcc22e3d8f87f4f6df93fd5c1de514254f6879ec6
- Host selected input: AirPods at 24000 Hz. Guest: 48000 Hz, mono.
- Guest virtio_snd.ko SHA256:
  245db509cb64b596e608d9ed5d86fb30235033d08b4bfcc495026252087e4476
- Guest snd-pcm.ko SHA256:
  240ba4a4d812bbb2ac1934e68069798f9d4ddff5ec6a34f4640bd2b223b90608
- Guest kernel image SHA256:
  d7fb220b0677f4f8e1c8e0ed307c05fa1feacf43c2d7f437952be2091954f395
- SIP enabled. No coreaudiod restart. No real microphone PCM saved.

Build/install the candidate guest kernel and modules, not a host macOS module.
Before loading virtio_snd, install this guest /etc/modprobe.d option line:
options virtio_snd capture_staging=1 capture_packed_status=1 capture_allow_short=1 capture_allow_empty=1 capture_pause_restart=1
Verify the five /sys/module/virtio_snd/parameters/capture_* values and the
loaded module/kernel identities. Do not unload a module used by other audio
clients. Use a dedicated disposable VM.

Inside that guest, verify `arecord -l`, `cat /proc/asound/cards` and /dev/snd.
Run Docker on this guest; mapping /dev/snd through a different Docker daemon
cannot expose this VM's sound card. Compose can map devices: ["/dev/snd:/dev/snd"].
An ALSA utility capture in such a container is a generic driver smoke test;
it is not the original private recorder implementation used in the recorded
acceptance and must not be presented as an exact replay of that application.

Generic smoke command (select the correct card/device first):
  arecord -D hw:0,0 -t raw -f FLOAT_LE -r 48000 -c 1 -d 70 /dev/null
This avoids retaining audio but does not measure nonzero samples or peaks.
Use a wall-clock timeout and check exit status plus stderr for xrun/overrun.
A clean zero-signal stream is not proof of a working physical microphone.

Hardware acceptance sequence to repeat with a metrics-capable recorder:
1. 6s / 600s / 6s, nonzero finite samples, exact expected frame counts,
   no error/status flags, bounded wall duration, clean stop and reopen.
2. SIGKILL only the dedicated test recorder after capture starts; verify
   exit 137, then two fresh 6s captures without restarting host audio.
3. Shut down the disposable VM, boot the same kernel/module image, verify
   identities again, repeat 6s / 70s / 6s. Do not restart coreaudiod.
4. Record source identity, format, frames, errors and elapsed time; avoid
   saving microphone PCM unless the tester explicitly chooses to do so.

Recorded results: 600s captured 28,800,000 frames in 600.088s, status=0,
non-finite=0, peak=1.73. SIGKILL/reopen and fresh VM boot capture passed.
These historical results were from the complete integrated snapshot3 source,
not standalone v3 or the generic ALSA smoke command above.

Not validated on that candidate: built-in microphone, route switching,
macOS sleep/wake, guest freeze/restore, other Docker Desktop VMMs, physical
non-coherent DMA/NOMMU hardware, acoustic calibration or absence of clipping.
