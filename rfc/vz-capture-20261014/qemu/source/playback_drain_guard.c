/* SPDX-License-Identifier: GPL-2.0-only */
#define _POSIX_C_SOURCE 200809L

#include <alsa/asoundlib.h>
#include <alloca.h>
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int configure_playback(snd_pcm_t *pcm,
			      snd_pcm_uframes_t *buffer_frames_out)
{
	snd_pcm_hw_params_t *hw;
	snd_pcm_sw_params_t *sw;
	snd_pcm_uframes_t period = 1024;
	snd_pcm_uframes_t buffer = period * 4;
	snd_pcm_uframes_t actual_buffer;
	snd_pcm_uframes_t actual_period;
	snd_pcm_uframes_t start_threshold;
	snd_pcm_uframes_t stop_threshold;
	snd_pcm_uframes_t avail_min = 1;
	unsigned int rate = 48000;
	int direction = 0;
	int rc;

	snd_pcm_hw_params_alloca(&hw);
	snd_pcm_sw_params_alloca(&sw);

	rc = snd_pcm_hw_params_any(pcm, hw);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params_set_access(pcm, hw,
					  SND_PCM_ACCESS_RW_INTERLEAVED);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params_set_format(pcm, hw, SND_PCM_FORMAT_S16_LE);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params_set_channels(pcm, hw, 2);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate, &direction);
	if (rc < 0)
		return rc;
	direction = 0;
	rc = snd_pcm_hw_params_set_period_size_near(pcm, hw, &period,
						    &direction);
	if (rc < 0)
		return rc;
	buffer = period * 4;
	rc = snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params(pcm, hw);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params_get_period_size(hw, &actual_period, &direction);
	if (rc < 0)
		return rc;
	rc = snd_pcm_hw_params_get_buffer_size(hw, &actual_buffer);
	if (rc < 0)
		return rc;

	rc = snd_pcm_sw_params_current(pcm, sw);
	if (rc < 0)
		return rc;
	rc = snd_pcm_sw_params_set_avail_min(pcm, sw, avail_min);
	if (rc < 0)
		return rc;
	start_threshold = 1;
	rc = snd_pcm_sw_params_set_start_threshold(pcm, sw, start_threshold);
	if (rc < 0)
		return rc;
	stop_threshold = actual_buffer;
	rc = snd_pcm_sw_params_set_stop_threshold(pcm, sw, stop_threshold);
	if (rc < 0)
		return rc;
	rc = snd_pcm_sw_params(pcm, sw);
	if (rc < 0)
		return rc;

	printf("CONFIG rate=%u format=S16_LE channels=2 period_frames=%" PRIu64
	       " buffer_frames=%" PRIu64 "\n",
	       rate, (uint64_t)actual_period, (uint64_t)actual_buffer);
	*buffer_frames_out = actual_buffer;
	return 0;
}

int main(int argc, char **argv)
{
	const char *device = argc > 1 ? argv[1] : "hw:0,0";
	snd_pcm_t *pcm = NULL;
	snd_pcm_uframes_t buffer_frames = 0;
	int16_t *samples = NULL;
	snd_pcm_sframes_t written;
	snd_pcm_sframes_t write_after_drain;
	snd_pcm_state_t state;
	int drain_rc;
	int rc;
	int exit_code = 1;

	if (argc > 2) {
		fprintf(stderr, "usage: %s [pcm-device]\n", argv[0]);
		return 64;
	}

	rc = snd_pcm_open(&pcm, device, SND_PCM_STREAM_PLAYBACK,
			  SND_PCM_NONBLOCK);
	if (rc < 0) {
		fprintf(stderr, "OPEN rc=%d error=%s\n", rc, snd_strerror(rc));
		return 77;
	}

	rc = configure_playback(pcm, &buffer_frames);
	if (rc < 0) {
		fprintf(stderr, "CONFIG_ERROR rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto out;
	}
	samples = calloc((size_t)buffer_frames * 2, sizeof(*samples));
	if (!samples) {
		fprintf(stderr, "ALLOC_ERROR\n");
		goto out;
	}
	for (snd_pcm_uframes_t i = 0; i < buffer_frames * 2; i++)
		samples[i] = (int16_t)((i % 127) * 64 - 4032);

	rc = snd_pcm_prepare(pcm);
	if (rc < 0) {
		fprintf(stderr, "PREPARE rc=%d error=%s\n", rc,
			snd_strerror(rc));
		goto out;
	}
	written = snd_pcm_writei(pcm, samples, buffer_frames);
	printf("WRITE initial_frames=%" PRIu64 " result=%" PRId64 "\n",
	       (uint64_t)buffer_frames, (int64_t)written);
	if (written != (snd_pcm_sframes_t)buffer_frames) {
		if (written < 0)
			fprintf(stderr, "INITIAL_WRITE_ERROR %s\n",
				snd_strerror((int)written));
		goto out;
	}

	drain_rc = snd_pcm_drain(pcm);
	state = snd_pcm_state(pcm);
	printf("DRAIN result=%d error=%s state=%s\n", drain_rc,
	       drain_rc < 0 ? snd_strerror(drain_rc) : "none",
	       snd_pcm_state_name(state));
	if (drain_rc != -EAGAIN || state != SND_PCM_STATE_DRAINING) {
		printf("VERDICT=INCONCLUSIVE reason=drain_did_not_leave_nonblocking_stream_draining\n");
		exit_code = 2;
		goto out;
	}

	write_after_drain = snd_pcm_writei(pcm, samples, 1);
	printf("WRITE_AFTER_DRAIN result=%" PRId64 " error=%s\n",
	       (int64_t)write_after_drain,
	       write_after_drain < 0 ? snd_strerror((int)write_after_drain) :
	       "none");
	if (write_after_drain == -EBADFD) {
		printf("VERDICT=PASS playback_write_rejected_while_draining\n");
		exit_code = 0;
	} else {
		printf("VERDICT=FAIL playback_write_was_not_rejected_with_EBADFD\n");
	}

out:
	if (pcm) {
		snd_pcm_drop(pcm);
		snd_pcm_close(pcm);
	}
	free(samples);
	return exit_code;
}
