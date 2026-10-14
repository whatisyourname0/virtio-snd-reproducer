/* SPDX-License-Identifier: GPL-2.0-only */
/* Host shims for the !CONFIG_MMU PCM buffer lifetime paths. */
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef unsigned int gfp_t;
typedef int spinlock_t;
typedef unsigned long snd_pcm_uframes_t;

#define GFP_KERNEL 0
#define GFP_ATOMIC 0
#define PAGE_SIZE 4096U
#define SNDRV_PCM_STREAM_PLAYBACK 0
#define SNDRV_PCM_STREAM_CAPTURE 1
#define VIRTIO_SND_PCM_F_MSG_POLLING 0
#define PCM_MSG_SG_XFER 0
#define PCM_MSG_SG_STATUS 1
#define PCM_MSG_SG_DATA 2
#define cpu_to_le32(value) ((u32)(value))
#define le32_to_cpu(value) ((u32)(value))
#define min_t(type, left, right) ((type)(left) < (type)(right) ? \
				      (type)(left) : (type)(right))
#define check_add_overflow(a, b, result) \
	__builtin_add_overflow((a), (b), (result))
#define check_mul_overflow(a, b, result) \
	__builtin_mul_overflow((a), (b), (result))
#define kzalloc_objs(object, count) \
	test_kzalloc(sizeof(object) * (count), GFP_KERNEL)
#define kzalloc_flex(object, member, count) \
	test_kzalloc(sizeof(object) + sizeof((object).member[0]) * (count), \
		     GFP_KERNEL)
#define guard(type) test_guard_##type
#define WARN_ON_ONCE(condition) test_warn_on(condition)

struct device { unsigned int unused; };
struct virtio_device { struct device dev; };
struct virtio_pcm_substream;
struct virtio_pcm_msg;
struct virtio_snd_pcm_xfer { u32 stream_id; };
struct virtio_snd_pcm_status { u32 status; u32 latency_bytes; };
struct snd_pcm_runtime {
	u8 *dma_area;
	size_t dma_bytes;
	size_t period_bytes;
	long delay;
};
struct snd_pcm_substream { struct snd_pcm_runtime *runtime; };
struct page {
	uintptr_t virtual_base;
	phys_addr_t physical_base;
	unsigned int refs;
};
struct scatterlist {
	struct page *page;
	void *address;
	unsigned int offset;
	unsigned int length;
	bool end;
};
struct work_struct { unsigned int schedules; };
struct wait_queue_head { unsigned int wakes; };
typedef struct wait_queue_head wait_queue_head_t;
struct virtqueue {
	void *cookies[8];
	struct scatterlist sgs[8][4];
	unsigned int pending;
	unsigned int add_calls;
	unsigned int fail_add_call;
	unsigned int kicks;
};
struct virtio_snd_queue { spinlock_t lock; struct virtqueue *vqueue; };
struct virtio_snd {
	struct virtio_device *vdev;
	struct virtio_snd_queue tx_queue;
	struct virtio_snd_queue rx_queue;
};
struct virtio_pcm_substream {
	struct virtio_snd *snd;
	struct snd_pcm_substream *substream;
	spinlock_t lock;
	u32 sid;
	u32 features;
	u32 direction;
	size_t buffer_bytes;
	size_t hw_ptr;
	bool xfer_enabled;
	bool xfer_xrun;
	struct virtio_pcm_msg **msgs;
	unsigned int nmsgs;
	unsigned int msg_count;
	size_t capture_ready;
	u64 capture_copied_total;
	u64 capture_acked_total;
	u64 capture_pointer_seen_total;
	u64 capture_notified_periods;
	u64 capture_callback_snapshot;
	bool capture_callback_snapshot_active;
	bool capture_staging;
	bool capture_started;
	unsigned int capture_empty;
	struct page **dma_pages;
	unsigned int ndma_pages;
	u8 *owned_dma_area;
	size_t owned_dma_bytes;
	struct work_struct elapsed_period;
	wait_queue_head_t msg_empty;
};
struct virtio_pcm_msg {
	struct virtio_pcm_substream *substream;
	struct virtio_snd_pcm_xfer xfer;
	struct virtio_snd_pcm_status status;
	size_t length;
	u8 *data;
	size_t data_offset;
	struct scatterlist sgs[];
};

static unsigned int allocation_calls;
static unsigned int fail_allocation_at;
static unsigned int outstanding_allocations;
static unsigned int wake_calls;
static unsigned int warning_calls;
static unsigned int ratelimited_errors;
static unsigned int assertion_count;
static unsigned int failure_count;
static const char *active_test;
static struct page page_cache[64];
static unsigned int page_cache_count;

struct allocation_record { void *pointer; bool active; };
static struct allocation_record allocation_records[128];

static void *test_kzalloc(size_t size, gfp_t flags)
{
	void *pointer;
	unsigned int i;

	(void)flags;
	allocation_calls++;
	if (fail_allocation_at && allocation_calls == fail_allocation_at)
		return NULL;
	pointer = calloc(1, size);
	if (!pointer)
		return NULL;
	for (i = 0; i < sizeof(allocation_records) / sizeof(allocation_records[0]);
	     i++) {
		if (!allocation_records[i].active) {
			allocation_records[i].pointer = pointer;
			allocation_records[i].active = true;
			outstanding_allocations++;
			return pointer;
		}
	}
	abort();
}

static void *kzalloc(size_t size, gfp_t flags)
{
	return test_kzalloc(size, flags);
}

static void *kcalloc(size_t count, size_t size, gfp_t flags)
{
	return test_kzalloc(count * size, flags);
}

static void kfree(void *pointer)
{
	unsigned int i;

	if (!pointer)
		return;
	for (i = 0; i < sizeof(allocation_records) / sizeof(allocation_records[0]);
	     i++) {
		if (allocation_records[i].active &&
		    allocation_records[i].pointer == pointer) {
			allocation_records[i].active = false;
			allocation_records[i].pointer = NULL;
			outstanding_allocations--;
			free(pointer);
			return;
		}
	}
	fprintf(stderr, "untracked kfree(%p)\n", pointer);
	abort();
}

static bool test_warn_on(bool condition)
{
	if (condition)
		warning_calls++;
	return condition;
}

static void test_guard_spinlock(spinlock_t *lock) { (void)lock; }
static void test_guard_spinlock_irqsave(spinlock_t *lock) { (void)lock; }

static void dev_err(const struct device *device, const char *format, ...)
{
	(void)device;
	(void)format;
}

static void dev_err_ratelimited(const struct device *device,
				const char *format, ...)
{
	(void)device;
	(void)format;
	ratelimited_errors++;
}

static void *vzalloc(size_t size)
{
	(void)size;
	fprintf(stderr, "unexpected staged capture allocation in NOMMU default-off test\n");
	abort();
}

static void vfree(void *pointer)
{
	if (pointer) {
		fprintf(stderr, "unexpected staged capture free in NOMMU default-off test\n");
		abort();
	}
}

static void wake_up_all(wait_queue_head_t *queue)
{
	queue->wakes++;
	wake_calls++;
}

static void schedule_work(struct work_struct *work) { work->schedules++; }
static long bytes_to_frames(struct snd_pcm_runtime *runtime, size_t bytes)
{
	(void)runtime;
	return (long)bytes;
}

static size_t snd_pcm_lib_period_bytes(struct snd_pcm_substream *substream)
{
	return substream->runtime->period_bytes;
}

static struct virtio_snd_queue *virtsnd_pcm_queue(
	struct virtio_pcm_substream *vss)
{
	return vss->direction == SNDRV_PCM_STREAM_PLAYBACK ?
		&vss->snd->tx_queue : &vss->snd->rx_queue;
}

static struct page *vmalloc_to_page(const void *pointer)
{
	uintptr_t address = (uintptr_t)pointer;
	uintptr_t base = address & ~(uintptr_t)(PAGE_SIZE - 1);
	unsigned int i;

	for (i = 0; i < page_cache_count; i++) {
		if (page_cache[i].virtual_base == base)
			return &page_cache[i];
	}
	if (page_cache_count == sizeof(page_cache) / sizeof(page_cache[0]))
		return NULL;
	page_cache[page_cache_count].virtual_base = base;
	page_cache[page_cache_count].physical_base = base;
	page_cache[page_cache_count].refs = 1;
	return &page_cache[page_cache_count++];
}

static bool is_vmalloc_addr(const void *pointer)
{
	(void)pointer;
	return false;
}

static unsigned int offset_in_page(const void *pointer)
{
	return (uintptr_t)pointer & (PAGE_SIZE - 1);
}

static phys_addr_t page_to_phys(struct page *page)
{
	return page->physical_base;
}

static phys_addr_t sg_phys(struct scatterlist *sg)
{
	return sg->page->physical_base + sg->offset;
}

static void sg_init_one(struct scatterlist *sg, const void *address,
			unsigned int length)
{
	memset(sg, 0, sizeof(*sg));
	sg->address = (void *)address;
	sg->length = length;
}

static void sg_set_page(struct scatterlist *sg, struct page *page,
			unsigned int length, unsigned int offset)
{
	memset(sg, 0, sizeof(*sg));
	sg->page = page;
	sg->address = (void *)(page->virtual_base + offset);
	sg->offset = offset;
	sg->length = length;
}

static void sg_mark_end(struct scatterlist *sg) { sg->end = true; }

static bool virtqueue_kick_prepare(struct virtqueue *vqueue)
{
	return vqueue->pending != 0;
}

static void virtqueue_notify(struct virtqueue *vqueue) { vqueue->kicks++; }

static int virtqueue_add_sgs(struct virtqueue *vqueue,
			     struct scatterlist **sgs, unsigned int out_num,
			     unsigned int in_num, void *cookie, gfp_t flags)
{
	unsigned int i, j;

	(void)flags;
	vqueue->add_calls++;
	if (vqueue->fail_add_call == vqueue->add_calls)
		return -EIO;
	if (vqueue->pending == sizeof(vqueue->cookies) /
						 sizeof(vqueue->cookies[0]))
		return -ENOSPC;
	i = vqueue->pending++;
	vqueue->cookies[i] = cookie;
	for (j = 0; j < out_num + in_num; j++)
		vqueue->sgs[i][j] = *sgs[j];
	return 0;
}

static void get_page(struct page *page) { page->refs++; }
static void put_page(struct page *page) { page->refs--; }

void virtsnd_pcm_msg_free(struct virtio_pcm_substream *vss);

/* Staged capture is disabled in these default-off NOMMU cases. */
static void virtsnd_pcm_capture_complete(struct virtio_pcm_msg *msg,
					 size_t written_bytes)
{
	(void)msg;
	(void)written_bytes;
	fprintf(stderr, "unexpected staged capture completion in NOMMU default-off test\n");
	abort();
}

/* EXTRACTED_NOMMU_FUNCTIONS */

struct fixture {
	struct virtio_device vdev;
	struct virtio_snd snd;
	struct virtqueue tx_vq, rx_vq;
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
};

static void check(bool condition, const char *expression, int line)
{
	assertion_count++;
	if (!condition) {
		fprintf(stderr, "FAIL [%s] %s at line %d\n", active_test,
			expression, line);
		failure_count++;
	}
}

#define CHECK(condition) check((condition), #condition, __LINE__)

static void fixture_init(struct fixture *fixture, unsigned int direction,
			 size_t periods, size_t period_bytes)
{
	size_t buffer_bytes = periods * period_bytes;

	memset(fixture, 0, sizeof(*fixture));
	page_cache_count = 0;
	fixture->runtime.dma_area = calloc(1, buffer_bytes);
	if (!fixture->runtime.dma_area)
		abort();
	fixture->runtime.dma_bytes = buffer_bytes;
	fixture->runtime.period_bytes = period_bytes;
	fixture->substream.runtime = &fixture->runtime;
	fixture->snd.vdev = &fixture->vdev;
	fixture->snd.tx_queue.vqueue = &fixture->tx_vq;
	fixture->snd.rx_queue.vqueue = &fixture->rx_vq;
	fixture->vss.snd = &fixture->snd;
	fixture->vss.substream = &fixture->substream;
	fixture->vss.direction = direction;
	fixture->vss.sid = 7;
	fixture->vss.buffer_bytes = buffer_bytes;
}

static void fixture_cleanup(struct fixture *fixture)
{
	virtsnd_pcm_msg_free(&fixture->vss);
	free(fixture->runtime.dma_area);
	fixture->runtime.dma_area = NULL;
	fixture->substream.runtime = NULL;
}

static unsigned char *sg_data(struct scatterlist *sg)
{
	if (sg->page)
		return (unsigned char *)(sg->page->virtual_base + sg->offset);
	return sg->address;
}

static unsigned int sg_total_bytes(struct virtio_pcm_msg *msg)
{
	unsigned int i, total = 0;

	for (i = 0; i < 8; i++) {
		struct scatterlist *sg = &msg->sgs[PCM_MSG_SG_DATA + i];

		if (!sg->length)
			break;
		total += sg->length;
		if (sg->end)
			break;
	}
	return total;
}

static void test_alloc_failure_unwinds_and_recovers(void)
{
	const unsigned int failures_to_inject[] = { 1, 2, 4 };
	unsigned int i;

	active_test = "allocation-unwind-owned-array-and-partial-message";
	for (i = 0; i < sizeof(failures_to_inject) /
					 sizeof(failures_to_inject[0]); i++) {
		struct fixture fixture;
		int rc;

		fixture_init(&fixture, SNDRV_PCM_STREAM_CAPTURE, 2, 64);
		allocation_calls = 0;
		fail_allocation_at = failures_to_inject[i];
		rc = virtsnd_pcm_msg_alloc(&fixture.vss, 2, 64);
		CHECK(rc == -ENOMEM);
		CHECK(fixture.vss.msgs == NULL);
		CHECK(fixture.vss.nmsgs == 0);
		CHECK(fixture.vss.owned_dma_area == NULL);
		CHECK(fixture.vss.owned_dma_bytes == 0);
		CHECK(outstanding_allocations == 0);

		fail_allocation_at = 0;
		allocation_calls = 0;
		CHECK(virtsnd_pcm_msg_alloc(&fixture.vss, 2, 64) == 0);
		CHECK(fixture.vss.owned_dma_area != NULL);
		CHECK(fixture.vss.owned_dma_bytes == 128);
		for (unsigned int j = 0; j < fixture.vss.owned_dma_bytes; j++)
			CHECK(fixture.vss.owned_dma_area[j] == 0);
		fixture_cleanup(&fixture);
		CHECK(outstanding_allocations == 0);
	}
}

static void test_playback_copies_entire_published_period(void)
{
	struct fixture fixture;
	unsigned int i;

	active_test = "playback-copy-full-period-after-fragments";
	fixture_init(&fixture, SNDRV_PCM_STREAM_PLAYBACK, 2, 64);
	memset(fixture.runtime.dma_area, 0x11, 128);
	CHECK(virtsnd_pcm_msg_alloc(&fixture.vss, 2, 64) == 0);
	CHECK(virtsnd_pcm_msg_send(&fixture.vss, 0, 32) == 0);
	CHECK(fixture.tx_vq.pending == 0);
	memset(fixture.runtime.dma_area, 0x22, 32);
	memset(fixture.runtime.dma_area + 32, 0x33, 32);
	CHECK(virtsnd_pcm_msg_send(&fixture.vss, 32, 32) == 0);
	CHECK(fixture.tx_vq.pending == 1);
	CHECK(fixture.vss.msg_count == 1);
	CHECK(sg_total_bytes(fixture.vss.msgs[0]) == 64);
	for (i = 0; i < 32; i++)
		CHECK(sg_data(&fixture.vss.msgs[0]->sgs[PCM_MSG_SG_DATA])[i] ==
		      0x22);
	for (; i < 64; i++)
		CHECK(sg_data(&fixture.vss.msgs[0]->sgs[PCM_MSG_SG_DATA])[i] ==
		      0x33);
	virtsnd_pcm_msg_complete(fixture.vss.msgs[0],
				 sizeof(fixture.vss.msgs[0]->status));
	CHECK(fixture.vss.msg_count == 0);
	fixture_cleanup(&fixture);
	CHECK(outstanding_allocations == 0);
}

static void test_capture_clamps_to_capacity_and_copyback_gate(void)
{
	struct fixture fixture;
	struct virtio_pcm_msg *msg;
	unsigned char *owned;

	active_test = "capture-copy-clamp-capacity";
	fixture_init(&fixture, SNDRV_PCM_STREAM_CAPTURE, 2, 64);
	CHECK(virtsnd_pcm_msg_alloc(&fixture.vss, 2, 64) == 0);
	msg = fixture.vss.msgs[0];
	owned = sg_data(&msg->sgs[PCM_MSG_SG_DATA]);
	memset(owned, 0x6d, 64);
	msg->length = 64;
	fixture.vss.msg_count = 1;
	fixture.vss.xfer_enabled = true;
	virtsnd_pcm_msg_complete(msg, sizeof(msg->status) + 80);
	CHECK(fixture.vss.hw_ptr == 64);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.runtime.dma_area[0] == 0x6d);
	CHECK(fixture.runtime.dma_area[63] == 0x6d);
	CHECK(fixture.runtime.dma_area[64] == 0);
	CHECK(fixture.vss.elapsed_period.schedules == 1);
	CHECK(ratelimited_errors == 1);
	fixture_cleanup(&fixture);
	CHECK(outstanding_allocations == 0);
}

static void test_capture_completion_after_alsa_buffer_free(void)
{
	struct fixture fixture;
	struct virtio_pcm_msg *msg;
	struct virtqueue *vqueue;
	u8 *owned;

	active_test = "capture-owned-buffer-after-alsa-free-stopped-no-copyback";
	fixture_init(&fixture, SNDRV_PCM_STREAM_CAPTURE, 2, 64);
	CHECK(virtsnd_pcm_msg_alloc(&fixture.vss, 2, 64) == 0);
	CHECK(virtsnd_pcm_msg_send(&fixture.vss, 0, 64) == 0);
	vqueue = fixture.rx_vq.pending ? &fixture.rx_vq : NULL;
	CHECK(vqueue != NULL);
	msg = vqueue ? vqueue->cookies[0] : NULL;
	CHECK(msg == fixture.vss.msgs[0]);
	owned = fixture.vss.owned_dma_area;
	CHECK(owned != NULL);
	fixture.vss.xfer_enabled = false;
	free(fixture.runtime.dma_area);
	fixture.runtime.dma_area = NULL;
	fixture.runtime.dma_bytes = 0;
	fixture.substream.runtime = NULL;
	CHECK(fixture.vss.owned_dma_area == owned);
	memset(sg_data(&msg->sgs[PCM_MSG_SG_DATA]), 0xa7, 64);
	virtsnd_pcm_msg_complete(msg, sizeof(msg->status) + 64);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.vss.hw_ptr == 64);
	CHECK(fixture.vss.elapsed_period.schedules == 0);
	CHECK(fixture.vss.msg_empty.wakes == 1);
	CHECK(fixture.vss.owned_dma_area == owned);
	CHECK(owned[0] == 0xa7 && owned[63] == 0xa7);
	virtsnd_pcm_msg_free(&fixture.vss);
	CHECK(fixture.vss.owned_dma_area == NULL);
	CHECK(fixture.vss.owned_dma_bytes == 0);
	CHECK(outstanding_allocations == 0);
}

#ifndef TEST_BASELINE
static void test_reset_cancel_accounting_and_wakeup(void)
{
	struct fixture fixture;
	struct virtio_pcm_msg first = { 0 }, second = { 0 };

	active_test = "reset-cancel-pending-count-and-wakeup";
	fixture_init(&fixture, SNDRV_PCM_STREAM_CAPTURE, 2, 64);
	CHECK(virtsnd_pcm_msg_alloc(&fixture.vss, 2, 64) == 0);
	first.substream = &fixture.vss;
	first.length = 64;
	second.substream = &fixture.vss;
	second.length = 64;
	fixture.vss.msg_count = 2;
	fixture.vss.xfer_enabled = true;
	fixture.vss.hw_ptr = 13;
	wake_calls = 0;
	virtsnd_pcm_msg_cancel(&first);
	CHECK(first.length == 0);
	CHECK(fixture.vss.msg_count == 1);
	CHECK(!fixture.vss.xfer_enabled);
	CHECK(fixture.vss.hw_ptr == 13);
	CHECK(fixture.vss.elapsed_period.schedules == 0);
	CHECK(wake_calls == 0);
	virtsnd_pcm_msg_cancel(&second);
	CHECK(second.length == 0);
	CHECK(fixture.vss.msg_count == 0);
	CHECK(fixture.vss.hw_ptr == 13);
	CHECK(fixture.vss.elapsed_period.schedules == 0);
	CHECK(wake_calls == 1);
	CHECK(warning_calls == 0);
	fixture_cleanup(&fixture);
	CHECK(outstanding_allocations == 0);
}
#endif

#ifdef TEST_BASELINE
int main(void)
{
	struct fixture fixture;
	int rc;

	active_test = "baseline-nommu-vmalloc-rejection";
	fixture_init(&fixture, SNDRV_PCM_STREAM_CAPTURE, 1, 64);
	rc = virtsnd_pcm_msg_alloc(&fixture.vss, 1, 64);
	if (rc != -EINVAL) {
		fprintf(stderr, "baseline did not reproduce NOMMU rejection rc=%d\n",
			rc);
		return 1;
	}
	if (fixture.vss.msgs || fixture.vss.owned_dma_area) {
		fprintf(stderr, "baseline allocated message storage unexpectedly\n");
		return 1;
	}
	printf("RESULT BASELINE_REPRODUCED rc=%d\n", rc);
	fixture_cleanup(&fixture);
	return 0;
}
#else
static void run_case(const char *name, void (*test)(void))
{
	unsigned int failures_before = failure_count;

	test();
	printf("CASE %s %s\n",
	       failure_count == failures_before ? "PASS" : "FAIL", name);
}

int main(void)
{
	run_case("allocation-unwind-and-recovery",
		 test_alloc_failure_unwinds_and_recovers);
	run_case("playback-full-period-copy",
		 test_playback_copies_entire_published_period);
	run_case("capture-capacity-clamp-and-copyback",
		 test_capture_clamps_to_capacity_and_copyback_gate);
	run_case("stopped-completion-after-alsa-free",
		 test_capture_completion_after_alsa_buffer_free);
	run_case("reset-cancel-count-and-wakeup",
		 test_reset_cancel_accounting_and_wakeup);
	if (failure_count) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertion_count, failure_count);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertion_count);
	return 0;
}
#endif
