/* SPDX-License-Identifier: GPL-2.0-only */
#define _GNU_SOURCE

#include <alsa/asoundlib.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t pause_requested;
static volatile sig_atomic_t recovery_requested;

static void on_signal(int signo)
{
	if (signo == SIGUSR1)
		pause_requested = 1;
	else if (signo == SIGUSR2)
		recovery_requested = 1;
}

static long elapsed_ms(const struct timespec *start,
		       const struct timespec *end)
{
	return (end->tv_sec - start->tv_sec) * 1000L +
		(end->tv_nsec - start->tv_nsec) / 1000000L;
}

static int install_signals(void)
{
	struct sigaction action;

	memset(&action, 0, sizeof(action));
	action.sa_handler = on_signal;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGUSR1, &action, NULL) ||
	    sigaction(SIGUSR2, &action, NULL)) {
		perror("sigaction");
		return -1;
	}
	return 0;
}

static int read_exact(snd_pcm_t *pcm, int16_t *samples,
		      snd_pcm_uframes_t frames, const char *stage)
{
	snd_pcm_uframes_t done = 0;

	while (done < frames) {
		snd_pcm_sframes_t rc = snd_pcm_readi(
			pcm, samples + done * 2, frames - done);

		if (rc < 0) {
			fprintf(stderr, "FAIL stage=%s readi_rc=%ld error=%s\n",
				stage, (long)rc, snd_strerror((int)rc));
			return -1;
		}
		if (rc == 0) {
			fprintf(stderr, "FAIL stage=%s readi_rc=0\n", stage);
			return -1;
		}
		done += (snd_pcm_uframes_t)rc;
	}
	return 0;
}

static int wait_for_signal(volatile sig_atomic_t *flag, const char *marker)
{
	sigset_t signals, previous_mask;

	sigemptyset(&signals);
	sigaddset(&signals, SIGUSR1);
	sigaddset(&signals, SIGUSR2);
	if (sigprocmask(SIG_BLOCK, &signals, &previous_mask) != 0) {
		perror("sigprocmask");
		return -1;
	}
	while (!*flag)
		sigsuspend(&previous_mask);
	if (sigprocmask(SIG_SETMASK, &previous_mask, NULL) != 0) {
		perror("sigprocmask");
		return -1;
	}
	printf("SIGNAL_RECEIVED marker=%s\n", marker);
	return 0;
}

int main(int argc, char **argv)
{
	const char *device = argc > 1 ? argv[1] : "hw:0,0";
	snd_pcm_t *pcm = NULL;
	snd_pcm_hw_params_t *hw;
	snd_pcm_sw_params_t *sw;
	snd_pcm_uframes_t period = 1024, buffer = 4096;
	snd_pcm_uframes_t actual_period, actual_buffer;
	unsigned int rate = 48000, actual_rate, actual_channels;
	int direction = 0, can_pause, rc;
	int16_t *samples = NULL;
	struct timespec start, end;

	setvbuf(stdout, NULL, _IONBF, 0);
	if (install_signals())
		return 2;

	rc = snd_pcm_open(&pcm, device, SND_PCM_STREAM_CAPTURE, 0);
	if (rc < 0) {
		fprintf(stderr, "FAIL stage=open rc=%d error=%s\n", rc,
			snd_strerror(rc));
		return 2;
	}
	snd_pcm_hw_params_alloca(&hw);
	if ((rc = snd_pcm_hw_params_any(pcm, hw)) < 0 ||
	    (rc = snd_pcm_hw_params_set_access(pcm, hw,
						 SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
	    (rc = snd_pcm_hw_params_set_format(pcm, hw,
						 SND_PCM_FORMAT_S16_LE)) < 0 ||
	    (rc = snd_pcm_hw_params_set_channels(pcm, hw, 2)) < 0 ||
	    (rc = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate,
						 &direction)) < 0 ||
	    (rc = snd_pcm_hw_params_set_period_size_near(pcm, hw, &period,
						    &direction)) < 0 ||
	    (rc = snd_pcm_hw_params_set_buffer_size_near(pcm, hw,
						     &buffer)) < 0) {
		fprintf(stderr, "FAIL stage=hw_params_config rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto fail;
	}
	can_pause = snd_pcm_hw_params_can_pause(hw);
	if (!can_pause) {
		fprintf(stderr, "FAIL stage=pause_capability can_pause=0\n");
		goto fail;
	}
	if ((rc = snd_pcm_hw_params(pcm, hw)) < 0) {
		fprintf(stderr, "FAIL stage=hw_params_apply rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto fail;
	}
	if ((rc = snd_pcm_hw_params_get_period_size(hw, &actual_period,
						    &direction)) < 0 ||
	    (rc = snd_pcm_hw_params_get_buffer_size(hw, &actual_buffer)) < 0 ||
	    (rc = snd_pcm_hw_params_get_rate(hw, &actual_rate,
						 &direction)) < 0 ||
	    (rc = snd_pcm_hw_params_get_channels(hw, &actual_channels)) < 0) {
		fprintf(stderr, "FAIL stage=geometry rc=%d\n", rc);
		goto fail;
	}
	if (actual_rate != 48000 || actual_channels != 2) {
		fprintf(stderr,
			"FAIL stage=actual_format rate=%u channels=%u\n",
			actual_rate, actual_channels);
		goto fail;
	}
	printf("PAUSE_RXFLUSH_CONFIG rate=%u format=S16_LE channels=%u "
	       "period=%lu buffer=%lu can_pause=%d\n",
	       actual_rate, actual_channels, (unsigned long)actual_period,
	       (unsigned long)actual_buffer, can_pause);
	snd_pcm_sw_params_alloca(&sw);
	if ((rc = snd_pcm_sw_params_current(pcm, sw)) < 0 ||
	    (rc = snd_pcm_sw_params_set_avail_min(pcm, sw,
						      actual_period / 2)) < 0 ||
	    (rc = snd_pcm_sw_params(pcm, sw)) < 0) {
		fprintf(stderr, "FAIL stage=sw_params rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto fail;
	}
	if (actual_period < 2 || actual_buffer < actual_period * 2) {
		fprintf(stderr,
			"FAIL stage=geometry period=%lu buffer=%lu\n",
			(unsigned long)actual_period, (unsigned long)actual_buffer);
		goto fail;
	}
	samples = calloc((size_t)actual_period * 2, sizeof(*samples));
	if (!samples) {
		perror("calloc");
		goto fail;
	}
	if ((rc = snd_pcm_prepare(pcm)) < 0 || (rc = snd_pcm_start(pcm)) < 0) {
		fprintf(stderr, "FAIL stage=start rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto fail;
	}
	if (read_exact(pcm, samples, actual_period / 2, "warmup"))
		goto fail;
	printf("PAUSE_RXFLUSH_READY pid=%ld period=%lu buffer=%lu "
	       "warmup_frames=%lu state=%s\n",
	       (long)getpid(), (unsigned long)actual_period,
	       (unsigned long)actual_buffer, (unsigned long)(actual_period / 2),
	       snd_pcm_state_name(snd_pcm_state(pcm)));
	if (wait_for_signal(&pause_requested, "pause"))
		goto fail;
	clock_gettime(CLOCK_MONOTONIC, &start);
	rc = snd_pcm_pause(pcm, 1);
	clock_gettime(CLOCK_MONOTONIC, &end);
	printf("PAUSE_RXFLUSH_RESULT rc=%d error=%s elapsed_ms=%ld state=%s\n",
	       rc, rc < 0 ? snd_strerror(rc) : "none", elapsed_ms(&start, &end),
	       snd_pcm_state_name(snd_pcm_state(pcm)));
	if (rc != -ETIMEDOUT) {
		fprintf(stderr, "FAIL stage=pause_expected_etimedout actual=%d\n", rc);
		goto fail;
	}
	if (wait_for_signal(&recovery_requested, "recovery"))
		goto fail;
	if ((rc = snd_pcm_prepare(pcm)) < 0) {
		fprintf(stderr, "FAIL stage=recovery_prepare rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto fail;
	}
	if ((rc = snd_pcm_start(pcm)) < 0) {
		fprintf(stderr, "FAIL stage=recovery_start rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto fail;
	}
	if (read_exact(pcm, samples, actual_period, "recovery"))
		goto fail;
	printf("PAUSE_RXFLUSH_RECOVERY_PASS frames=%lu state=%s\n",
	       (unsigned long)actual_period,
	       snd_pcm_state_name(snd_pcm_state(pcm)));
	free(samples);
	/* Keep the substream open through QEMU shutdown to avoid a final RELEASE. */
	for (;;)
		pause();

fail:
	free(samples);
	if (pcm)
		snd_pcm_close(pcm);
	return 2;
}
