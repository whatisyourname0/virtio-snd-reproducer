/* SPDX-License-Identifier: GPL-2.0-only */
/* Host shims for source-extracted virtio-snd PCM and ALSA teardown paths. */
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* TEST_DEFINES */

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef unsigned int gfp_t;
typedef size_t snd_pcm_uframes_t;
typedef int snd_pcm_format_t;
typedef int spinlock_t;
typedef int wait_queue_head_t;

#define GFP_ATOMIC 0
#define GFP_KERNEL 0
#define PAGE_SIZE 64U
#define SNDRV_PCM_STREAM_PLAYBACK 0
#define SNDRV_PCM_STREAM_CAPTURE 1
#define VIRTIO_SND_PCM_F_MSG_POLLING 0
#define VIRTIO_SND_R_PCM_RELEASE 0x0206U
#define cpu_to_le32(value) ((u32)(value))
#define le32_to_cpu(value) ((u32)(value))
#define min_t(type, left, right) ((type)(left) < (type)(right) ? \
				      (type)(left) : (type)(right))
#define WARN_ON_ONCE(condition) test_warn_on(condition)
#define guard(type) test_guard_##type
#define scoped_guard(type, lock) \
	for (unsigned int __guard_once = ((void)(lock), 1U); \
	     __guard_once; __guard_once = 0)
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
#define kzalloc_objs(pointer, count) \
	((typeof(&(pointer)))test_calloc((count), sizeof(pointer)))
#define kzalloc_flex(pointer, member, count) \
	((typeof(&(pointer)))test_flex_alloc(sizeof(pointer), \
					  sizeof(((typeof(&(pointer)))0)->member[0]), \
					  (count)))
#define wait_event_interruptible_timeout(queue, condition, timeout) \
	test_wait_event((queue), (condition), (timeout))
#define check_add_overflow(a, b, result) __builtin_add_overflow((a), (b), (result))
#define check_mul_overflow(a, b, result) __builtin_mul_overflow((a), (b), (result))

struct page {
	unsigned char *data;
	phys_addr_t phys;
	unsigned int refs;
	bool backing_freed;
};
struct scatterlist {
	struct page *page;
	void *address;
	unsigned int offset;
	unsigned int length;
	bool end;
};
struct work_struct { unsigned int schedules; };
struct snd_pcm_hardware { unsigned int unused; };
struct snd_pcm_indirect { size_t sw_buffer_size, hw_buffer_size; };
struct snd_pcm_runtime {
	unsigned char *dma_area;
	size_t dma_bytes;
	size_t period_bytes;
	bool stop_operating;
	long delay;
};
enum virtio_pcm_pause_restart_state {
	VIRTSND_PCM_PAUSE_RESTART_NONE,
	VIRTSND_PCM_PAUSE_RESTART_CONFIGURED,
	VIRTSND_PCM_PAUSE_RESTART_SET_PARAMS_SUBMITTED,
	VIRTSND_PCM_PAUSE_RESTART_PREPARE_SUBMITTED,
	VIRTSND_PCM_PAUSE_RESTART_PREPARED,
	VIRTSND_PCM_PAUSE_RESTART_START_SUBMITTED,
	VIRTSND_PCM_PAUSE_RESTART_RUNNING,
	VIRTSND_PCM_PAUSE_RESTART_STOP_SUBMITTED,
	VIRTSND_PCM_PAUSE_RESTART_STOPPED,
	VIRTSND_PCM_PAUSE_RESTART_RELEASE_SUBMITTED,
	VIRTSND_PCM_PAUSE_RESTART_RELEASE_SENT,
	VIRTSND_PCM_PAUSE_RESTART_RELEASED,
	VIRTSND_PCM_PAUSE_RESTART_RESET_REQUIRED,
};
struct snd_pcm_substream;
struct virtio_pcm_substream;
struct virtio_pcm_msg;
struct virtio_snd_msg;
struct virtio_snd_pcm_xfer { u32 stream_id; };
struct virtio_snd_pcm_status { u32 status; u32 latency_bytes; };
struct snd_pcm_ops {
	int (*hw_free)(struct snd_pcm_substream *substream);
	int (*sync_stop)(struct snd_pcm_substream *substream);
};
struct snd_pcm_substream {
	struct snd_pcm_runtime *runtime;
	struct snd_pcm_ops *ops;
	void *private_data;
	unsigned int stream;
	bool managed_buffer_alloc;
};
struct virtqueue {
	struct scatterlist sgs[8][4];
	unsigned int nouts[8];
	unsigned int nins[8];
	void *cookies[8];
	unsigned int pending;
	unsigned int kicks;
};
struct virtio_snd_queue { spinlock_t lock; struct virtqueue *vqueue; };
struct virtio_device { struct { unsigned int unused; } dev; };
struct virtio_snd {
	struct virtio_device *vdev;
	struct virtio_snd_queue tx_queue;
	struct virtio_snd_queue rx_queue;
};

static inline struct virtio_snd_queue *
virtsnd_pcm_queue(struct virtio_pcm_substream *vss);
static inline size_t snd_pcm_lib_period_bytes(struct snd_pcm_substream *substream)
{
	return substream->runtime->period_bytes;
}
static inline void *snd_pcm_substream_chip(struct snd_pcm_substream *substream)
{
	return substream->private_data;
}
bool virtsnd_capture_pause_restart;

_Noreturn static void unexpected_default_off_helper(const char *name)
{
	fprintf(stderr, "unexpected default-off helper call: %s\n", name);
	abort();
}

static int virtsnd_pcm_pause_restart_sync_stop(
	struct snd_pcm_substream *substream)
{
	(void)substream;
	unexpected_default_off_helper("pause_restart_sync_stop");
}

static void virtsnd_pcm_capture_complete(struct virtio_pcm_msg *msg,
					size_t written_bytes)
{
	(void)msg;
	(void)written_bytes;
	unexpected_default_off_helper("capture_complete");
}

static void *vzalloc(size_t size)
{
	(void)size;
	unexpected_default_off_helper("staged_capture_vzalloc");
}

static void vfree(void *pointer)
{
	if (pointer)
		unexpected_default_off_helper("staged_capture_vfree");
}
static void test_guard_spinlock_irqsave(spinlock_t *lock) { (void)lock; }
static void test_guard_spinlock(spinlock_t *lock) { (void)lock; }
static void cancel_work_sync(struct work_struct *work) { (void)work; }
static void schedule_work(struct work_struct *work) { work->schedules++; }
static long bytes_to_frames(struct snd_pcm_runtime *runtime, unsigned int bytes)
{
	(void)runtime;
	return bytes;
}
static unsigned int test_wakes;
static unsigned int test_warnings;
static unsigned int test_ratelimited_errors;
static bool test_warn_on(bool condition)
{
	if (condition)
		test_warnings++;
	return condition;
}
static void wake_up_all(wait_queue_head_t *queue)
{
	(void)queue;
	test_wakes++;
}
static unsigned int virtsnd_msg_timeout_ms = 1;
static unsigned int msecs_to_jiffies(unsigned int milliseconds)
{
	return milliseconds;
}
static long test_wait_result;
static long test_wait_event(wait_queue_head_t queue, bool condition,
			    unsigned int timeout)
{
	(void)queue;
	(void)timeout;
	return condition ? 1 : test_wait_result;
}
static int release_result;
static struct virtio_snd_msg *virtsnd_pcm_ctl_msg_alloc(
	struct virtio_pcm_substream *vss, unsigned int command, gfp_t gfp)
{
	(void)vss;
	(void)command;
	(void)gfp;
	return (struct virtio_snd_msg *)1;
}
static int virtsnd_ctl_msg_send_sync(struct virtio_snd *snd,
				     struct virtio_snd_msg *msg)
{
	(void)snd;
	(void)msg;
	return release_result;
}
static void dev_err(const void *device, const char *format, ...)
{
	(void)device;
	(void)format;
}
static void dev_warn(const void *device, const char *format, ...)
{
	(void)device;
	(void)format;
}
static void dev_err_ratelimited(const void *device, const char *format, ...)
{
	(void)device;
	(void)format;
	test_ratelimited_errors++;
}

static unsigned char *test_vmap;
static size_t test_vmap_size;
static struct page *test_pages;
static size_t test_page_count;
static unsigned int test_gets;
static unsigned int test_puts;
static unsigned int test_alloc_calls;
static unsigned int test_fail_alloc_at;
static unsigned int test_freed_pages;

static void *test_calloc(size_t count, size_t size)
{
	test_alloc_calls++;
	if (test_fail_alloc_at && test_alloc_calls == test_fail_alloc_at)
		return NULL;
	return calloc(count, size);
}
static void *test_flex_alloc(size_t base, size_t element, size_t count)
{
	test_alloc_calls++;
	if (test_fail_alloc_at && test_alloc_calls == test_fail_alloc_at)
		return NULL;
	return calloc(1, base + element * count);
}
static void *kcalloc(size_t count, size_t size, gfp_t flags)
{
	(void)flags;
	return test_calloc(count, size);
}
static void kfree(void *pointer) { free(pointer); }
static bool is_vmalloc_addr(const void *pointer)
{
	uintptr_t address = (uintptr_t)pointer;
	uintptr_t base = (uintptr_t)test_vmap;

	return test_vmap && address >= base && address < base + test_vmap_size;
}
static struct page *vmalloc_to_page(const void *pointer)
{
	uintptr_t offset = (uintptr_t)pointer - (uintptr_t)test_vmap;
	size_t index = offset / PAGE_SIZE;

	if (!test_vmap || index >= test_page_count)
		return NULL;
	return &test_pages[index];
}
static unsigned int offset_in_page(const void *pointer)
{
	return ((uintptr_t)pointer - (uintptr_t)test_vmap) % PAGE_SIZE;
}
static phys_addr_t page_to_phys(struct page *page) { return page->phys; }
static phys_addr_t sg_phys(struct scatterlist *sg)
{
	return sg->page->phys + sg->offset;
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
	sg->length = length;
	sg->offset = offset;
}
static void sg_mark_end(struct scatterlist *sg) { sg->end = true; }
static void get_page(struct page *page)
{
	page->refs++;
	test_gets++;
}
static void put_page(struct page *page)
{
	if (page->refs)
		page->refs--;
	test_puts++;
	if (!page->refs && !page->backing_freed) {
		free(page->data);
		page->backing_freed = true;
		test_freed_pages++;
	}
}
static bool virtqueue_kick_prepare(struct virtqueue *vqueue)
{
	return vqueue->pending != 0;
}
static void virtqueue_notify(struct virtqueue *vqueue) { vqueue->kicks++; }
static int virtqueue_add_sgs(struct virtqueue *vqueue,
			     struct scatterlist **sgs, unsigned int nouts,
			     unsigned int nins, void *cookie, gfp_t flags)
{
	unsigned int i;

	(void)flags;
	if (vqueue->pending == 8)
		return -ENOSPC;
	i = vqueue->pending++;
	vqueue->nouts[i] = nouts;
	vqueue->nins[i] = nins;
	vqueue->cookies[i] = cookie;
	for (unsigned int j = 0; j < nouts + nins; j++)
		vqueue->sgs[i][j] = *sgs[j];
	return 0;
}
static unsigned char *sg_data(struct scatterlist *sg)
{
	return sg->page ? sg->page->data + sg->offset : sg->address;
}
static void test_vfree_runtime(struct snd_pcm_runtime *runtime)
{
	if (!runtime->dma_area)
		return;
	for (size_t i = 0; i < test_page_count; i++)
		put_page(&test_pages[i]);
	free(test_vmap);
	test_vmap = NULL;
	test_vmap_size = 0;
	test_page_count = 0;
	runtime->dma_area = NULL;
	runtime->dma_bytes = 0;
}
static int snd_pcm_lib_free_pages(struct snd_pcm_substream *substream)
{
	test_vfree_runtime(substream->runtime);
	return 0;
}
static int snd_pcm_sync_stop(struct snd_pcm_substream *substream, bool state)
{
	(void)state;
	return substream->ops->sync_stop(substream);
}
static void test_pages_init(size_t count)
{
	test_vmap_size = count * PAGE_SIZE;
	if (posix_memalign((void **)&test_vmap, PAGE_SIZE, test_vmap_size))
		abort();
	memset(test_vmap, 0x3c, test_vmap_size);
	test_pages = calloc(count, sizeof(*test_pages));
	if (!test_pages)
		abort();
	test_page_count = count;
	for (size_t i = 0; i < count; i++) {
		test_pages[i].data = malloc(PAGE_SIZE);
		if (!test_pages[i].data)
			abort();
		memcpy(test_pages[i].data, test_vmap + i * PAGE_SIZE, PAGE_SIZE);
		test_pages[i].phys = 0x10000000ULL + i * 0x100000ULL;
		test_pages[i].refs = 1;
	}
}
static void test_pages_dispose(void)
{
	for (size_t i = 0; i < test_page_count; i++) {
		if (!test_pages[i].backing_freed)
			free(test_pages[i].data);
	}
	free(test_pages);
	test_pages = NULL;
	test_page_count = 0;
	free(test_vmap);
	test_vmap = NULL;
	test_vmap_size = 0;
}
static unsigned int assertions;
static unsigned int failures;
static const char *active_test;
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

void virtsnd_pcm_msg_free(struct virtio_pcm_substream *vss);

/* EXTRACTED_DMA_FUNCTIONS */

static inline struct virtio_snd_queue *
virtsnd_pcm_queue(struct virtio_pcm_substream *vss)
{
	return vss->direction == SNDRV_PCM_STREAM_PLAYBACK ?
		&vss->snd->tx_queue : &vss->snd->rx_queue;
}

static void test_fixture_init(struct virtio_pcm_substream *vss,
			      struct snd_pcm_substream *substream,
			      struct snd_pcm_runtime *runtime,
			      struct virtio_snd *snd,
			      struct virtio_device *vdev,
			      struct virtqueue *tx, struct virtqueue *rx,
			      struct snd_pcm_ops *ops, unsigned int direction,
			      size_t periods)
{
	memset(vss, 0, sizeof(*vss));
	memset(substream, 0, sizeof(*substream));
	memset(runtime, 0, sizeof(*runtime));
	memset(snd, 0, sizeof(*snd));
	memset(vdev, 0, sizeof(*vdev));
	memset(tx, 0, sizeof(*tx));
	memset(rx, 0, sizeof(*rx));
	test_gets = test_puts = test_freed_pages = 0;
	test_alloc_calls = 0;
	test_fail_alloc_at = 0;
	test_wait_result = 0;
	test_wakes = 0;
	test_warnings = 0;
	test_ratelimited_errors = 0;
	release_result = 0;
	substream->runtime = runtime;
	substream->ops = ops;
	substream->private_data = vss;
	substream->stream = direction;
	substream->managed_buffer_alloc = true;
	runtime->dma_area = test_vmap;
	runtime->dma_bytes = periods * PAGE_SIZE;
	runtime->period_bytes = PAGE_SIZE;
	snd->vdev = vdev;
	snd->tx_queue.vqueue = tx;
	snd->rx_queue.vqueue = rx;
	vss->snd = snd;
	vss->direction = direction;
	vss->substream = substream;
}

static void late_device_write(struct virtqueue *vqueue)
{
	struct virtio_pcm_msg *msg = vqueue->cookies[0];
	struct scatterlist *data = &msg->sgs[2];
	volatile unsigned char *bytes = sg_data(data);

	for (unsigned int i = 0; i < data->length; i++)
		bytes[i] = (unsigned char)(0xa0U + i);
	vqueue->pending = 0;
	virtsnd_pcm_msg_complete(msg, data->length + sizeof(msg->status));
}
static void late_device_read(struct virtqueue *vqueue)
{
	struct virtio_pcm_msg *msg = vqueue->cookies[0];
	struct scatterlist *data = &msg->sgs[2];
	volatile unsigned char *bytes = sg_data(data);
	volatile unsigned char value = bytes[0];

	(void)value;
	vqueue->pending = 0;
	virtsnd_pcm_msg_complete(msg, sizeof(msg->status));
}
static void setup_one(struct virtio_pcm_substream *vss,
		      struct snd_pcm_substream *substream,
		      struct snd_pcm_runtime *runtime,
		      struct virtio_snd *snd, struct virtio_device *vdev,
		      struct virtqueue *tx, struct virtqueue *rx,
		      struct snd_pcm_ops *ops, unsigned int direction)
{
	test_pages_init(1);
	test_fixture_init(vss, substream, runtime, snd, vdev, tx, rx, ops,
			  direction, 1);
}
static void expect_baseline_uaf(const char *mode)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime *runtime = calloc(1, sizeof(*runtime));
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue tx, rx;
	struct snd_pcm_ops ops = {
		.hw_free = virtsnd_pcm_hw_free,
		.sync_stop = virtsnd_pcm_sync_stop,
	};
	struct virtqueue *vqueue;

	active_test = mode;
	if (!runtime)
		abort();
	setup_one(&vss, &substream, runtime, &snd, &vdev, &tx, &rx, &ops,
		  !strcmp(mode, "late-playback") ? SNDRV_PCM_STREAM_PLAYBACK :
		  SNDRV_PCM_STREAM_CAPTURE);
	CHECK(virtsnd_pcm_msg_alloc(&vss, 1, PAGE_SIZE) == 0);
	CHECK(virtsnd_pcm_msg_send(&vss, 0, PAGE_SIZE) == 0);
	vqueue = virtsnd_pcm_queue(&vss)->vqueue;
	CHECK(vqueue->pending == 1);
	(void)do_hw_free(&substream);
	/* ALSA close releases runtime after the driver returns from hw_free. */
	free(runtime);
	/* Keep the dangling pointer as close does; sync_stop disabled transfers. */
	if (!strcmp(mode, "late-playback"))
		late_device_read(vqueue);
	else
		late_device_write(vqueue);
}

#ifdef TEST_CANDIDATE
static void test_capture_late_completion_recovery(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime *runtime = calloc(1, sizeof(*runtime));
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue tx, rx;
	struct snd_pcm_ops ops = {
		.hw_free = virtsnd_pcm_hw_free,
		.sync_stop = virtsnd_pcm_sync_stop,
	};
	struct virtqueue *vqueue;
	unsigned int gets;

	active_test = "capture-late-close-drain-next-normal";
	test_pages_init(1);
	test_fixture_init(&vss, &substream, runtime, &snd, &vdev, &tx, &rx,
			  &ops, SNDRV_PCM_STREAM_CAPTURE, 1);
	CHECK(virtsnd_pcm_msg_alloc(&vss, 1, PAGE_SIZE) == 0);
	CHECK(vss.ndma_pages == 1);
	CHECK(test_pages[0].refs == 2);
	CHECK(virtsnd_pcm_msg_send(&vss, 0, PAGE_SIZE) == 0);
	vqueue = virtsnd_pcm_queue(&vss)->vqueue;
	CHECK(vqueue->pending == 1);
	CHECK(do_hw_free(&substream) == 0);
	CHECK(runtime->dma_area == NULL);
	CHECK(test_pages[0].refs == 1);
	CHECK(vss.msg_count == 1);
	gets = test_gets;
	free(runtime);
	/* A late capture DMA writes through the pinned page, not the vmap alias. */
	late_device_write(vqueue);
	CHECK(test_gets == gets);
	CHECK(vss.msg_count == 0);
	CHECK(test_pages[0].data[0] == 0xa0);
	/* Reclaim old message storage only after the device completion. */
	virtsnd_pcm_msg_free(&vss);
	CHECK(vss.ndma_pages == 0);
	CHECK(test_pages[0].refs == 0);
	CHECK(test_gets + 1 == test_puts);
	virtsnd_pcm_msg_free(&vss);
	CHECK(test_gets + 1 == test_puts);
	test_pages_dispose();

	/* Reopen with fresh pages, then verify an ordinary transfer and teardown. */
	runtime = calloc(1, sizeof(*runtime));
	CHECK(runtime != NULL);
	test_pages_init(1);
	test_fixture_init(&vss, &substream, runtime, &snd, &vdev, &tx, &rx,
			  &ops, SNDRV_PCM_STREAM_CAPTURE, 1);
	CHECK(virtsnd_pcm_msg_alloc(&vss, 1, PAGE_SIZE) == 0);
	CHECK(virtsnd_pcm_msg_send(&vss, 0, PAGE_SIZE) == 0);
	late_device_write(virtsnd_pcm_queue(&vss)->vqueue);
	CHECK(vss.msg_count == 0);
	CHECK(do_hw_free(&substream) == 0);
	CHECK(test_gets + 1 == test_puts);
	free(runtime);
	test_pages_dispose();
}

static void test_playback_late_completion(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime *runtime = calloc(1, sizeof(*runtime));
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue tx, rx;
	struct snd_pcm_ops ops = {
		.hw_free = virtsnd_pcm_hw_free,
		.sync_stop = virtsnd_pcm_sync_stop,
	};
	struct virtqueue *vqueue;

	active_test = "playback-late-close-drain";
	test_pages_init(1);
	test_fixture_init(&vss, &substream, runtime, &snd, &vdev, &tx, &rx,
			  &ops, SNDRV_PCM_STREAM_PLAYBACK, 1);
	CHECK(virtsnd_pcm_msg_alloc(&vss, 1, PAGE_SIZE) == 0);
	CHECK(virtsnd_pcm_msg_send(&vss, 0, PAGE_SIZE) == 0);
	vqueue = virtsnd_pcm_queue(&vss)->vqueue;
	CHECK(do_hw_free(&substream) == 0);
	CHECK(test_pages[0].refs == 1);
	free(runtime);
	late_device_read(vqueue);
	CHECK(vss.msg_count == 0);
	virtsnd_pcm_msg_free(&vss);
	CHECK(test_gets + 1 == test_puts);
	test_pages_dispose();
}

static void test_partial_alloc_and_repeated_free(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue tx, rx;
	struct snd_pcm_ops ops = {
		.hw_free = virtsnd_pcm_hw_free,
		.sync_stop = virtsnd_pcm_sync_stop,
	};

	active_test = "page-array-allocation-failure-no-pins";
	test_pages_init(3);
	test_fixture_init(&vss, &substream, &runtime, &snd, &vdev, &tx, &rx,
			  &ops, SNDRV_PCM_STREAM_CAPTURE, 3);
	test_fail_alloc_at = test_alloc_calls + 1;
	CHECK(virtsnd_pcm_msg_alloc(&vss, 3, PAGE_SIZE) == -ENOMEM);
	CHECK(vss.msgs == NULL);
	CHECK(vss.nmsgs == 0);
	CHECK(vss.dma_pages == NULL);
	CHECK(vss.ndma_pages == 0);
	CHECK(test_gets == 0);
	for (size_t i = 0; i < 3; i++)
		CHECK(test_pages[i].refs == 1);

	active_test = "partial-alloc-balanced-page-refs";
	/* Fail the second period message after the page refs and first msg exist. */
	test_fail_alloc_at = 0;
	test_fail_alloc_at = test_alloc_calls + 4;
	CHECK(virtsnd_pcm_msg_alloc(&vss, 3, PAGE_SIZE) == -ENOMEM);
	CHECK(vss.msgs == NULL);
	CHECK(vss.nmsgs == 0);
	CHECK(vss.dma_pages == NULL);
	CHECK(vss.ndma_pages == 0);
	CHECK(test_gets == 3);
	CHECK(test_puts == 3);
	for (size_t i = 0; i < 3; i++)
		CHECK(test_pages[i].refs == 1);
	virtsnd_pcm_msg_free(&vss);
	CHECK(test_gets == test_puts);

	/* A later open can allocate and release the same pages normally. */
	test_fail_alloc_at = 0;
	active_test = "partial-allocation-recovery-next-normal";
	CHECK(virtsnd_pcm_msg_alloc(&vss, 3, PAGE_SIZE) == 0);
	CHECK(vss.ndma_pages == 3);
	for (size_t i = 0; i < 3; i++)
		CHECK(test_pages[i].refs == 2);
	virtsnd_pcm_msg_free(&vss);
	CHECK(vss.ndma_pages == 0);
	CHECK(test_gets == test_puts);
	for (size_t i = 0; i < 3; i++)
		CHECK(test_pages[i].refs == 1);
	test_pages_dispose();
}

static void test_page_count_overflow_and_size_bounds(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue tx, rx;
	struct snd_pcm_ops ops = {
		.hw_free = virtsnd_pcm_hw_free,
		.sync_stop = virtsnd_pcm_sync_stop,
	};

	active_test = "buffer-size-bounds";
	test_pages_init(1);
	test_fixture_init(&vss, &substream, &runtime, &snd, &vdev, &tx, &rx,
			  &ops, SNDRV_PCM_STREAM_CAPTURE, 1);
	runtime.dma_bytes = 1;
	CHECK(virtsnd_pcm_msg_alloc(&vss, UINT_MAX, UINT_MAX) < 0);
	CHECK(vss.dma_pages == NULL);
	CHECK(test_gets == 0);
	CHECK(vss.msgs == NULL);
	test_pages_dispose();
}

static void test_page_pin_released_only_after_free(void)
{
	struct virtio_pcm_substream vss;
	struct snd_pcm_substream substream;
	struct snd_pcm_runtime runtime;
	struct virtio_snd snd;
	struct virtio_device vdev;
	struct virtqueue tx, rx;
	struct snd_pcm_ops ops = {
		.hw_free = virtsnd_pcm_hw_free,
		.sync_stop = virtsnd_pcm_sync_stop,
	};

	active_test = "partial-params-reset-cleanup";
	test_pages_init(2);
	test_fixture_init(&vss, &substream, &runtime, &snd, &vdev, &tx, &rx,
			  &ops, SNDRV_PCM_STREAM_CAPTURE, 2);
	CHECK(virtsnd_pcm_msg_alloc(&vss, 2, PAGE_SIZE) == 0);
	CHECK(test_gets == 2);
	CHECK(vss.ndma_pages == 2);
	virtsnd_pcm_msg_free(&vss);
	CHECK(test_puts == 2);
	CHECK(test_gets == test_puts);
	virtsnd_pcm_msg_free(&vss);
	CHECK(test_puts == 2);
	test_pages_dispose();
}

static void test_reset_cancel_retires_tokens_without_completion(void)
{
	struct virtio_pcm_substream vss = { 0 };
	struct virtio_pcm_msg first = { 0 }, second = { 0 };
	unsigned int scheduled = vss.elapsed_period.schedules;

	active_test = "reset-cancel-pending-count-and-wakeup";
	vss.msg_count = 2;
	vss.xfer_enabled = true;
	vss.hw_ptr = 17;
	first.substream = &vss;
	first.length = PAGE_SIZE;
	second.substream = &vss;
	second.length = PAGE_SIZE;
	test_wakes = 0;
	test_warnings = 0;

	virtsnd_pcm_msg_cancel(&first);
	CHECK(first.length == 0);
	CHECK(vss.msg_count == 1);
	CHECK(!vss.xfer_enabled);
	CHECK(vss.hw_ptr == 17);
	CHECK(vss.elapsed_period.schedules == scheduled);
	CHECK(test_wakes == 0);

	virtsnd_pcm_msg_cancel(&second);
	CHECK(second.length == 0);
	CHECK(vss.msg_count == 0);
	CHECK(vss.hw_ptr == 17);
	CHECK(vss.elapsed_period.schedules == scheduled);
	CHECK(test_wakes == 1);
	CHECK(test_warnings == 0);
}
#endif

int main(int argc, char **argv)
{
#ifdef TEST_BASELINE
	if (argc != 2) {
		fprintf(stderr, "expected baseline scenario\n");
		return 2;
	}
	expect_baseline_uaf(argv[1]);
	return 0;
#else
	(void)argc;
	(void)argv;
	test_capture_late_completion_recovery();
	test_playback_late_completion();
	test_partial_alloc_and_repeated_free();
	test_page_count_overflow_and_size_bounds();
	test_page_pin_released_only_after_free();
	test_reset_cancel_retires_tokens_without_completion();
	if (failures) {
		fprintf(stderr, "RESULT FAIL assertions=%u failures=%u\n",
			assertions, failures);
		return 1;
	}
	printf("RESULT PASS assertions=%u\n", assertions);
	return 0;
#endif
}
