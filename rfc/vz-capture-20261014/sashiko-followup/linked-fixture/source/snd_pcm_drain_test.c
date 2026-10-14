// SPDX-License-Identifier: GPL-2.0
/* Test-only ALSA capture PCM for drain pointer-error controls. */
#include <linux/atomic.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/module.h>
#include <linux/string.h>
#include <sound/core.h>
#include <sound/memalloc.h>
#include <sound/pcm.h>

#define DRAIN_TEST_SUBSTREAMS 2

static int index = -1;
module_param(index, int, 0444);
MODULE_PARM_DESC(index, "ALSA card index");

static char *id = "DrainTest";
module_param(id, charp, 0444);
MODULE_PARM_DESC(id, "ALSA card identifier");

/* Set after START to keep the XRUN pointer path out of setup/start calls. */
static int fault_substream = -1;
module_param(fault_substream, int, 0644);
MODULE_PARM_DESC(fault_substream, "Substream that reports pointer XRUN, or -1");

static bool trace_armed;
module_param(trace_armed, bool, 0644);
MODULE_PARM_DESC(trace_armed, "Enable pointer-call trace after START");

static int pointer_calls[DRAIN_TEST_SUBSTREAMS];
module_param_array(pointer_calls, int, NULL, 0444);
MODULE_PARM_DESC(pointer_calls, "Total pointer calls by substream");

static int armed_pointer_calls[DRAIN_TEST_SUBSTREAMS];
module_param_array(armed_pointer_calls, int, NULL, 0444);
MODULE_PARM_DESC(armed_pointer_calls, "Pointer calls after trace_armed");

struct drain_test_stream {
	atomic_t started;
	atomic_t pointer_calls;
	atomic_t armed_pointer_calls;
	atomic_t logged_armed;
};

static struct snd_card *drain_test_card;
static struct snd_pcm *drain_test_pcm;
static struct device *drain_test_parent;
static struct drain_test_stream drain_test_streams[DRAIN_TEST_SUBSTREAMS];

static const struct snd_pcm_hardware drain_test_hw = {
	.info = SNDRV_PCM_INFO_INTERLEAVED | SNDRV_PCM_INFO_BLOCK_TRANSFER,
	.formats = SNDRV_PCM_FMTBIT_S16_LE,
	.rates = SNDRV_PCM_RATE_48000,
	.rate_min = 48000,
	.rate_max = 48000,
	.channels_min = 1,
	.channels_max = 1,
	.buffer_bytes_max = 65536,
	.period_bytes_min = 256,
	.period_bytes_max = 8192,
	.periods_min = 2,
	.periods_max = 8,
};

static int drain_test_open(struct snd_pcm_substream *substream)
{
	struct snd_pcm_runtime *runtime = substream->runtime;

	runtime->hw = drain_test_hw;
	runtime->private_data = &drain_test_streams[substream->number];
	return snd_pcm_hw_constraint_integer(runtime,
					     SNDRV_PCM_HW_PARAM_PERIODS);
}

static int drain_test_close(struct snd_pcm_substream *substream)
{
	struct drain_test_stream *stream = substream->runtime->private_data;

	atomic_set(&stream->started, 0);
	return 0;
}

static int drain_test_prepare(struct snd_pcm_substream *substream)
{
	struct drain_test_stream *stream = substream->runtime->private_data;
	unsigned int number = substream->number;

	atomic_set(&stream->started, 0);
	atomic_set(&stream->pointer_calls, 0);
	atomic_set(&stream->armed_pointer_calls, 0);
	atomic_set(&stream->logged_armed, 0);
	WRITE_ONCE(pointer_calls[number], 0);
	WRITE_ONCE(armed_pointer_calls[number], 0);
	return 0;
}

static int drain_test_trigger(struct snd_pcm_substream *substream, int cmd)
{
	struct drain_test_stream *stream = substream->runtime->private_data;

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		atomic_set(&stream->started, 1);
		return 0;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		atomic_set(&stream->started, 0);
		return 0;
	default:
		return -EINVAL;
	}
}

static snd_pcm_uframes_t drain_test_pointer(struct snd_pcm_substream *substream)
{
	struct drain_test_stream *stream = substream->runtime->private_data;
	unsigned int number = substream->number;
	int calls = atomic_inc_return(&stream->pointer_calls);
	bool armed = READ_ONCE(trace_armed);
	bool started = atomic_read(&stream->started);
	int armed_calls = 0;
	bool fault;

	WRITE_ONCE(pointer_calls[number], calls);
	if (armed) {
		armed_calls = atomic_inc_return(&stream->armed_pointer_calls);
		WRITE_ONCE(armed_pointer_calls[number], armed_calls);
		if (atomic_cmpxchg(&stream->logged_armed, 0, 1) == 0)
			pr_info("snd_pcm_drain_test: pointer substream=%u started=%u armed_calls=%d fault_substream=%d\n",
				number, started, armed_calls,
				READ_ONCE(fault_substream));
	}

	fault = armed && started && READ_ONCE(fault_substream) == number;
	return fault ? SNDRV_PCM_POS_XRUN : 0;
}

static const struct snd_pcm_ops drain_test_ops = {
	.open = drain_test_open,
	.close = drain_test_close,
	.ioctl = snd_pcm_lib_ioctl,
	.prepare = drain_test_prepare,
	.trigger = drain_test_trigger,
	.pointer = drain_test_pointer,
};

static int __init drain_test_init(void)
{
	unsigned int i;
	int err;

	for (i = 0; i < DRAIN_TEST_SUBSTREAMS; i++) {
		atomic_set(&drain_test_streams[i].started, 0);
		atomic_set(&drain_test_streams[i].pointer_calls, 0);
		atomic_set(&drain_test_streams[i].armed_pointer_calls, 0);
		atomic_set(&drain_test_streams[i].logged_armed, 0);
	}

	drain_test_parent = root_device_register("snd-pcm-drain-test");
	if (IS_ERR(drain_test_parent)) {
		err = PTR_ERR(drain_test_parent);
		drain_test_parent = NULL;
		return err;
	}
	err = snd_card_new(drain_test_parent, index, id, THIS_MODULE, 0,
			   &drain_test_card);
	if (err < 0)
		goto error_parent;
	strscpy(drain_test_card->driver, "snd_pcm_drain_test",
		sizeof(drain_test_card->driver));
	strscpy(drain_test_card->shortname, "PCM Drain Test",
		sizeof(drain_test_card->shortname));
	strscpy(drain_test_card->longname,
		"Synthetic two-substream capture PCM drain test",
		sizeof(drain_test_card->longname));

	err = snd_pcm_new(drain_test_card, "Drain Test PCM", 0, 0,
			  DRAIN_TEST_SUBSTREAMS, &drain_test_pcm);
	if (err < 0)
		goto error_card;
	snd_pcm_set_ops(drain_test_pcm, SNDRV_PCM_STREAM_CAPTURE,
			&drain_test_ops);
	err = snd_pcm_set_managed_buffer_all(drain_test_pcm,
					     SNDRV_DMA_TYPE_VMALLOC,
					     NULL, 0, 65536);
	if (err < 0)
		goto error_card;
	drain_test_pcm->info_flags = 0;
	drain_test_pcm->private_data = drain_test_card;
	strscpy(drain_test_pcm->name, "Drain Test Capture",
		sizeof(drain_test_pcm->name));

	err = snd_card_register(drain_test_card);
	if (err < 0)
		goto error_card;
	pr_info("snd_pcm_drain_test: registered card=%d device=0 substreams=%d\n",
		drain_test_card->number, DRAIN_TEST_SUBSTREAMS);
	return 0;

error_card:
	snd_card_free(drain_test_card);
	drain_test_card = NULL;
error_parent:
	root_device_unregister(drain_test_parent);
	drain_test_parent = NULL;
	return err;
}

static void __exit drain_test_exit(void)
{
	if (drain_test_card)
		snd_card_free(drain_test_card);
	if (drain_test_parent)
		root_device_unregister(drain_test_parent);
}

module_init(drain_test_init);
module_exit(drain_test_exit);
MODULE_DESCRIPTION("Test-only synthetic ALSA capture PCM for drain pointer errors");
MODULE_LICENSE("GPL");
