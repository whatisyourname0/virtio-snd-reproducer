/* SPDX-License-Identifier: GPL-2.0-only */
/* Source-extracted control-order check for virtio-snd device removal. */
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_STAGES 128
#define VIRTIO_SND_VQ_MAX 4
#define VIRTIO_SND_VQ_TX 2
#define VIRTIO_SND_VQ_RX 3
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags) = 0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); (void)(flags); } while (0)

struct virtio_device;
struct virtio_config_ops {
	void (*del_vqs)(struct virtio_device *vdev);
};
struct virtio_device {
	void *priv;
	const struct virtio_config_ops *config;
};
struct snd_card { unsigned int unused; };
struct work_struct { unsigned int unused; };
struct virtio_pcm_substream { struct work_struct elapsed_period; };
struct virtqueue { unsigned int mapped; unsigned int detached; };
struct virtio_snd_queue { int lock; struct virtqueue *vqueue; };
struct virtio_snd {
	struct virtio_device *vdev;
	struct virtio_snd_queue queues[VIRTIO_SND_VQ_MAX];
	struct snd_card *card;
	struct virtio_pcm_substream *substreams;
	unsigned int nsubstreams;
	void *event_msgs;
	bool ctl_msgs_blocked;
};

enum stage {
	STAGE_DISABLE_EVENTS,
	STAGE_CARD_FREE,
	STAGE_CANCEL_CONTROL,
	STAGE_DEVICE_RESET,
	STAGE_DELETE_VQS,
	STAGE_CONTROL_CLEANUP,
};

static enum stage stages[MAX_STAGES];
static unsigned int stage_count;
static int card_free_control_rc;
static unsigned int pcm_cancellations;
static bool device_reset;
static unsigned int assertions;
static unsigned int failures;
static const char *active_test = "remove-control-order";

static void record_stage(enum stage stage)
{
	if (stage_count < MAX_STAGES)
		stages[stage_count++] = stage;
}

static void check(bool condition, const char *expression, int line)
{
	assertions++;
	if (!condition) {
		fprintf(stderr, "FAIL [%s] %s at line %d\n", active_test,
			expression, line);
		failures++;
	}
}

#define CHECK(condition) check((condition), #condition, __LINE__)

static void virtsnd_disable_event_vq(struct virtio_snd *snd)
{
	(void)snd;
	record_stage(STAGE_DISABLE_EVENTS);
}

static void snd_card_free(struct snd_card *card)
{
	struct virtio_snd *snd = NULL;

	(void)card;
	/* Removal fixture tracks the device globally, as the ALSA close path does. */
	extern struct virtio_snd *active_snd;
	snd = active_snd;
	record_stage(STAGE_CARD_FREE);
	card_free_control_rc = snd->ctl_msgs_blocked ? -1 : 0;
}

static void virtsnd_ctl_msg_cancel_all(struct virtio_snd *snd)
{
	record_stage(STAGE_CANCEL_CONTROL);
	snd->ctl_msgs_blocked = true;
}

static void virtio_reset_device(struct virtio_device *vdev)
{
	(void)vdev;
	device_reset = true;
	record_stage(STAGE_DEVICE_RESET);
}

static void test_del_vqs(struct virtio_device *vdev)
{
	struct virtio_snd *snd = vdev->priv;
	unsigned int i;

	CHECK(device_reset);
	for (i = 0; i < VIRTIO_SND_VQ_MAX; i++)
		CHECK(!snd->queues[i].vqueue || !snd->queues[i].vqueue->mapped);
	record_stage(STAGE_DELETE_VQS);
}

static void virtsnd_ctl_msg_cleanup(struct virtio_snd *snd)
{
	(void)snd;
	record_stage(STAGE_CONTROL_CLEANUP);
}

static void cancel_work_sync(struct work_struct *work)
{
	(void)work;
}

static void virtsnd_pcm_msg_free(struct virtio_pcm_substream *vss)
{
	(void)vss;
}

static void kfree(void *pointer)
{
	free(pointer);
}

static const struct virtio_config_ops test_config_ops = {
	.del_vqs = test_del_vqs,
};
struct virtio_snd *active_snd;

static void *virtqueue_detach_unused_buf(struct virtqueue *vq)
{
	CHECK(device_reset);
	if (!vq->mapped)
		return NULL;
	vq->mapped--;
	vq->detached++;
	return vq;
}

static void virtsnd_pcm_msg_cancel(void *msg)
{
	CHECK(msg != NULL);
	pcm_cancellations++;
}

/* EXTRACTED_VIRTSND_REMOVE */

static void test_remove_and_freeze_unmap_all_buffers(void)
{
	unsigned int mode, i;

	for (mode = 0; mode < 2; mode++) {
		struct virtqueue vqs[VIRTIO_SND_VQ_MAX] = { 0 };
		struct virtio_snd snd = { 0 };
		struct virtio_device vdev = { .priv = &snd, .config = &test_config_ops };

		active_test = mode ? "freeze-unmaps" : "remove-unmaps";
		snd.vdev = &vdev;
		active_snd = &snd;
		device_reset = false;
		pcm_cancellations = 0;
		for (i = 0; i < VIRTIO_SND_VQ_MAX; i++) {
			vqs[i].mapped = 2;
			snd.queues[i].vqueue = &vqs[i];
		}
		if (mode)
			CHECK(virtsnd_freeze(&vdev) == 0);
		else
			virtsnd_remove(&vdev);
		for (i = 0; i < VIRTIO_SND_VQ_MAX; i++) {
			CHECK(vqs[i].mapped == 0);
			CHECK(snd.queues[i].vqueue == NULL);
		}
		CHECK(pcm_cancellations == 4);
	}
}

#ifdef VIRTSND_RESET_VQS_AVAILABLE
static void test_reset_unmaps_all_queues_and_tolerates_partial_probe(void)
{
	struct virtqueue vqs[VIRTIO_SND_VQ_MAX] = { 0 };
	struct virtio_snd snd = { 0 };
	struct virtio_device vdev = { .priv = &snd, .config = &test_config_ops };
	unsigned int i;

	active_test = "reset-unmaps-before-delete";
	snd.vdev = &vdev;
	device_reset = false;
	pcm_cancellations = 0;
	for (i = 0; i < VIRTIO_SND_VQ_MAX; i++) {
		vqs[i].mapped = i + 1;
		snd.queues[i].vqueue = &vqs[i];
	}
	virtsnd_reset_vqs(&snd);
	for (i = 0; i < VIRTIO_SND_VQ_MAX; i++) {
		CHECK(vqs[i].mapped == 0);
		CHECK(vqs[i].detached == i + 1);
		CHECK(snd.queues[i].vqueue == NULL);
	}
	CHECK(pcm_cancellations == 7);
	virtsnd_reset_vqs(&snd);
	CHECK(pcm_cancellations == 7);
	vqs[0].mapped = 1;
	snd.queues[0].vqueue = &vqs[0];
	virtsnd_reset_vqs(&snd);
	CHECK(vqs[0].mapped == 0);
	CHECK(pcm_cancellations == 7);
}
#endif

static void test_remove_card_work_precedes_control_gate_and_queue_teardown(void)
{
	static const enum stage expected[] = {
		STAGE_DISABLE_EVENTS,
		STAGE_CARD_FREE,
		STAGE_CANCEL_CONTROL,
		STAGE_DEVICE_RESET,
		STAGE_DELETE_VQS,
		STAGE_CONTROL_CLEANUP,
	};
	struct snd_card card = { 0 };
	struct virtio_snd snd = { .card = &card };
	struct virtio_device vdev = {
		.priv = &snd,
		.config = &test_config_ops,
	};
	unsigned int i;

	snd.vdev = &vdev;
	active_snd = &snd;
	stage_count = 0;
	card_free_control_rc = -99;
	virtsnd_remove(&vdev);
	CHECK(card_free_control_rc == 0);
	CHECK(snd.ctl_msgs_blocked);
	CHECK(stage_count == sizeof(expected) / sizeof(expected[0]));
	for (i = 0; i < sizeof(expected) / sizeof(expected[0]) &&
	     i < stage_count; i++)
		CHECK(stages[i] == expected[i]);
}

int main(void)
{
	test_remove_card_work_precedes_control_gate_and_queue_teardown();
	test_remove_and_freeze_unmap_all_buffers();
#ifdef VIRTSND_RESET_VQS_AVAILABLE
	test_reset_unmaps_all_queues_and_tolerates_partial_probe();
#endif
	if (failures) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertions, failures);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertions);
	return 0;
}
