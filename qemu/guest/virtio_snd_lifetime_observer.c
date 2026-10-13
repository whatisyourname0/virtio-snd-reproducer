// SPDX-License-Identifier: GPL-2.0-only

#include <linux/delay.h>
#include <linux/errno.h>
#include <linux/atomic.h>
#include <linux/kprobes.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/scatterlist.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/workqueue.h>
#include <linux/virtio_snd.h>
#include <sound/core.h>
#include <asm/ptrace.h>

#define MAX_CAPTURE_PAGES 64
#define FIRST_READ_DELAY_MS 1200
#define LATE_READ_DELAY_MS 9800

static bool watch_capture = true;
module_param(watch_capture, bool, 0644);
MODULE_PARM_DESC(watch_capture, "Record one real virtio-snd capture descriptor");
static bool watch_control = true;
module_param(watch_control, bool, 0644);
MODULE_PARM_DESC(watch_control, "Record one PCM_INFO control response payload");

static struct page *capture_pages[MAX_CAPTURE_PAGES];
static unsigned int capture_offsets[MAX_CAPTURE_PAGES];
static unsigned int capture_page_count;
static unsigned int capture_bytes;
static u32 capture_stream_id;
static bool capture_saved;
static atomic_t capture_retired = ATOMIC_INIT(0);
static void *control_response_data;
static unsigned int control_response_size;
static bool control_response_saved;
static atomic_t control_completion_pending = ATOMIC_INIT(0);
static atomic_t control_callback_count = ATOMIC_INIT(0);
static atomic_t control_query_started = ATOMIC_INIT(0);
static int (*query_info_fn)(void *snd, int command, int start_id, int count,
			    size_t size, void *info);
static DEFINE_SPINLOCK(capture_lock);
static void first_read_work_fn(struct work_struct *work);
static void late_read_work_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(first_read_work, first_read_work_fn);
static DECLARE_DELAYED_WORK(late_read_work, late_read_work_fn);

static void read_capture_pages(struct work_struct *work, const char *phase)
{
	unsigned long flags;
	unsigned int count, i;
	u32 stream_id;
	unsigned int bytes;

	spin_lock_irqsave(&capture_lock, flags);
	count = capture_page_count;
	stream_id = capture_stream_id;
	bytes = capture_bytes;
	spin_unlock_irqrestore(&capture_lock, flags);

	if (!count)
		return;

	pr_info("VIRTSND_LIFETIME %s_BEGIN stream=%u pages=%u bytes=%u\n",
		phase, stream_id, count, bytes);
	for (i = 0; i < count; ++i) {
		struct page *page = READ_ONCE(capture_pages[i]);
		unsigned int offset = READ_ONCE(capture_offsets[i]);
		volatile u8 *addr;
		u8 value;

		if (!page)
			continue;
		addr = (volatile u8 *)page_address(page) + offset;
		value = READ_ONCE(*addr);
		pr_info("VIRTSND_LIFETIME %s_PAGE index=%u pfn=%lx ref=%d offset=%u value=%02x\n",
			phase, i, page_to_pfn(page), page_ref_count(page),
			offset, value);
	}
	pr_info("VIRTSND_LIFETIME %s_END\n", phase);
}

static void first_read_work_fn(struct work_struct *work)
{
	read_capture_pages(work, "AFTER_RELEASE_TIMEOUT");
	schedule_delayed_work(&late_read_work,
			      msecs_to_jiffies(LATE_READ_DELAY_MS -
					       FIRST_READ_DELAY_MS));
}

static void late_read_work_fn(struct work_struct *work)
{
	read_capture_pages(work, "AFTER_BACKEND_COMPLETION");
}

static int add_sgs_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
	struct scatterlist **sgs;
	struct scatterlist *sg;
	struct virtio_snd_pcm_xfer *xfer;
	struct page *pages[MAX_CAPTURE_PAGES];
	unsigned int offsets[MAX_CAPTURE_PAGES];
	unsigned int count = 0;
	unsigned int out_sgs, in_sgs;
	unsigned int total = 0;
	unsigned int i;
	unsigned long flags;

	if ((!READ_ONCE(watch_capture) || READ_ONCE(capture_saved)) &&
	    (!READ_ONCE(watch_control) || READ_ONCE(control_response_saved)))
		return 0;

	/* arm64: virtqueue_add_sgs(vq, sgs, out_sgs, in_sgs, data, gfp). */
	sgs = (struct scatterlist **)(unsigned long)regs->regs[1];
	out_sgs = (unsigned int)regs->regs[2];
	in_sgs = (unsigned int)regs->regs[3];
	if (!sgs || out_sgs != 1 || in_sgs != 2)
		return 0;

	sg = READ_ONCE(sgs[0]);
	if (!sg || sg->length < sizeof(struct virtio_snd_hdr) || !sg_page(sg))
		return 0;
	if (!sg_virt(sg))
		return 0;
	if (le32_to_cpu(READ_ONCE(*(u32 *)sg_virt(sg))) ==
	    VIRTIO_SND_R_PCM_INFO && READ_ONCE(watch_control) &&
	    !READ_ONCE(control_response_saved)) {
		struct scatterlist *response_sg = READ_ONCE(sgs[2]);
		unsigned long data;
		unsigned long flags;

		if (!response_sg || !sg_page(response_sg) || !response_sg->length)
			return 0;
		data = (unsigned long)sg_virt(response_sg);
		if (!data)
			return 0;
		spin_lock_irqsave(&capture_lock, flags);
		if (!control_response_saved) {
			WRITE_ONCE(control_response_data, (void *)data);
			control_response_size = response_sg->length;
			control_response_saved = true;
		}
		spin_unlock_irqrestore(&capture_lock, flags);
		pr_info("VIRTSND_LIFETIME CTL_SG_SAVED addr=%px bytes=%u\n",
			(void *)data, response_sg->length);
		return 0;
	}

	if (!READ_ONCE(watch_capture) || READ_ONCE(capture_saved) ||
	    sg->length != sizeof(*xfer))
		return 0;
	xfer = sg_virt(sg);
	if (!xfer || le32_to_cpu(READ_ONCE(xfer->stream_id)) > 1)
		return 0;

	/* PCM capture has an 8-byte transfer header and a multi-SG input payload. */
	sg = READ_ONCE(sgs[1]);
	if (!sg || !sg_page(sg))
		return 0;
	for (;;) {
		unsigned int span;
		unsigned int page_index;

		if (!sg_page(sg))
			return 0;
		span = sg->offset + sg->length;
		for (page_index = 0; page_index < DIV_ROUND_UP(span, PAGE_SIZE);
		     ++page_index) {
			if (count == MAX_CAPTURE_PAGES)
				break;
			pages[count] = pfn_to_page(page_to_pfn(sg_page(sg)) +
						   page_index);
			offsets[count] = page_index ? 0 : sg->offset;
			count++;
		}
		total += sg->length;
		if (sg_is_last(sg) || count == MAX_CAPTURE_PAGES)
			break;
		sg = sg_next(sg);
		if (!sg)
			return 0;
	}
	if (!count || total < PAGE_SIZE)
		return 0;

	spin_lock_irqsave(&capture_lock, flags);
	if (!capture_saved) {
		for (i = 0; i < count; ++i) {
			WRITE_ONCE(capture_pages[i], pages[i]);
			WRITE_ONCE(capture_offsets[i], offsets[i]);
		}
		capture_page_count = count;
		capture_bytes = total;
		capture_stream_id = le32_to_cpu(READ_ONCE(xfer->stream_id));
		capture_saved = true;
	}
	spin_unlock_irqrestore(&capture_lock, flags);

	pr_info("VIRTSND_LIFETIME CAPTURE_SG_SAVED stream=%u pages=%u bytes=%u first_pfn=%lx first_ref=%d\n",
		le32_to_cpu(READ_ONCE(xfer->stream_id)), count, total,
		page_to_pfn(pages[0]), page_ref_count(pages[0]));
	return 0;
}

static int pcm_hw_free_return_handler(struct kretprobe_instance *instance,
					      struct pt_regs *regs)
{
	long rc = regs_return_value(regs);

	if (!READ_ONCE(capture_saved) || atomic_cmpxchg(&capture_retired, 0, 1))
		return 0;

	pr_info("VIRTSND_LIFETIME PCM_HW_FREE_RETURN rc=%ld pages=%u\n",
		rc, READ_ONCE(capture_page_count));
	schedule_delayed_work(&first_read_work,
			      msecs_to_jiffies(FIRST_READ_DELAY_MS));
	return 0;
}

static int ctl_notify_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
	void *data;
	u8 value;
	int count;
	int pending;

	count = atomic_inc_return(&control_callback_count);
	pending = atomic_xchg(&control_completion_pending, 0);
	pr_info("VIRTSND_LIFETIME CTL_NOTIFY_ENTER count=%d pending=%d\n",
		count, pending);
	if (!pending)
		return 0;
	data = READ_ONCE(control_response_data);
	if (!data)
		return 0;
	value = READ_ONCE(*(volatile u8 *)data);
	pr_info("VIRTSND_LIFETIME CTL_LATE_NOTIFY_READ value=%02x\n", value);
	return 0;
}

static int run_control_query_set(const char *value,
				 const struct kernel_param *param)
{
	struct snd_card *card;
	struct virtio_snd_pcm_info *info;
	int requested;
	int rc;

	(void)param;
	rc = kstrtoint(value, 0, &requested);
	if (rc)
		return rc;
	if (requested != 1)
		return -EINVAL;
	if (!query_info_fn || atomic_cmpxchg(&control_query_started, 0, 1))
		return -EBUSY;
	card = snd_card_ref(0);
	if (!card)
		return -ENODEV;
	info = kzalloc(sizeof(*info), GFP_KERNEL);
	if (!info) {
		snd_card_unref(card);
		return -ENOMEM;
	}
	pr_info("VIRTSND_LIFETIME CTL_QUERY_BEGIN command=PCM_INFO\n");
	rc = query_info_fn(card->private_data, VIRTIO_SND_R_PCM_INFO, 0, 1,
			   sizeof(*info), info);
	pr_info("VIRTSND_LIFETIME CTL_QUERY_RESULT rc=%d\n", rc);
	if (rc == -ETIMEDOUT && READ_ONCE(control_response_saved)) {
		atomic_set(&control_completion_pending, 1);
		pr_info("VIRTSND_LIFETIME CTL_QUERY_TIMEOUT rc=%d bytes=%u\n",
			rc, READ_ONCE(control_response_size));
	}
	kfree(info);
	snd_card_unref(card);
	return 0;
}

static const struct kernel_param_ops run_control_query_ops = {
	.set = run_control_query_set,
	.get = NULL,
};
module_param_cb(run_control_query, &run_control_query_ops, NULL, 0200);
MODULE_PARM_DESC(run_control_query,
		 "Issue one real PCM_INFO control query for the lifetime fault test");

static struct kprobe add_sgs_probe = {
	.symbol_name = "virtqueue_add_sgs",
	.pre_handler = add_sgs_pre_handler,
};

static struct kprobe ctl_notify_probe = {
	.symbol_name = "virtsnd_ctl_notify_cb",
	.pre_handler = ctl_notify_pre_handler,
};

static struct kretprobe pcm_hw_free_probe = {
	.kp.symbol_name = "virtsnd_pcm_hw_free",
	.handler = pcm_hw_free_return_handler,
	.maxactive = 8,
};

static int __init lifetime_observer_init(void)
{
	struct kprobe query_symbol = {
		.symbol_name = "virtsnd_ctl_query_info",
	};
	int rc;

	INIT_DELAYED_WORK(&first_read_work, first_read_work_fn);
	INIT_DELAYED_WORK(&late_read_work, late_read_work_fn);
	rc = register_kprobe(&query_symbol);
	if (rc)
		return rc;
	query_info_fn = (typeof(query_info_fn))query_symbol.addr;
	unregister_kprobe(&query_symbol);
	rc = register_kprobe(&add_sgs_probe);
	if (rc)
		return rc;
	rc = register_kprobe(&ctl_notify_probe);
	if (rc) {
		unregister_kprobe(&add_sgs_probe);
		return rc;
	}
	rc = register_kretprobe(&pcm_hw_free_probe);
	if (rc) {
		unregister_kprobe(&ctl_notify_probe);
		unregister_kprobe(&add_sgs_probe);
		return rc;
	}
	pr_info("VIRTSND_LIFETIME OBSERVER_READY watch_capture=%u watch_control=%u\n",
		READ_ONCE(watch_capture), READ_ONCE(watch_control));
	return 0;
}

static void __exit lifetime_observer_exit(void)
{
	unregister_kretprobe(&pcm_hw_free_probe);
	unregister_kprobe(&ctl_notify_probe);
	unregister_kprobe(&add_sgs_probe);
	cancel_delayed_work_sync(&first_read_work);
	cancel_delayed_work_sync(&late_read_work);
	pr_info("VIRTSND_LIFETIME OBSERVER_EXIT\n");
}

module_init(lifetime_observer_init);
module_exit(lifetime_observer_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Guest-side virtio-snd buffer lifetime observer");
