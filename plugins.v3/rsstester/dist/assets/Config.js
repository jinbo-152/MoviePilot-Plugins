import { importShared } from './__federation_fn_import-054b33c3.js';

const vueNs = (await importShared('vue')) || {};
const V = typeof vueNs.h === 'function'
  ? vueNs
  : (vueNs.default && typeof vueNs.default.h === 'function' ? vueNs.default : (typeof vueNs === 'function' ? vueNs : {}));
const h = V.h;
const reactive = V.reactive;
const ref = V.ref;
const computed = V.computed;
const onMounted = V.onMounted;
const onUnmounted = typeof V.onUnmounted === 'function' ? V.onUnmounted : () => {};

const INPUT_STYLE = {
  width: '110px',
  padding: '6px 10px',
  borderRadius: '8px',
  border: '1px solid rgba(128,128,128,0.4)',
  background: 'transparent',
  color: 'inherit',
  fontSize: '13px',
};

const SEARCH_STYLE = {
  width: '100%',
  padding: '6px 10px',
  borderRadius: '8px',
  border: '1px solid rgba(128,128,128,0.4)',
  background: 'transparent',
  color: 'inherit',
  fontSize: '13px',
  boxSizing: 'border-box',
};

const BUTTON_STYLE = {
  padding: '6px 14px',
  borderRadius: '8px',
  border: '1px solid rgba(128,128,128,0.4)',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
  fontSize: '13px',
};

const MUTED = { fontSize: '12px', color: '#8a8a8a' };

/** 兼容宿主 API 的多种响应包装，只取包含站点数组的业务负载。 */
const sitesPayload = (response) => {
  if (!response || typeof response !== 'object') return null;
  const candidates = [response, response.data];
  for (const candidate of candidates) {
    if (candidate && typeof candidate === 'object' && Array.isArray(candidate.sites)) {
      return candidate;
    }
  }
  return null;
};

const errorText = (exc) => {
  const detail = exc && exc.response && exc.response.data;
  if (detail && typeof detail === 'object') {
    return detail.message || detail.detail || detail.msg || '';
  }
  return (exc && exc.message) || '';
};

const Config = {
  name: 'RssTesterConfig',
  props: {
    initialConfig: { type: Object, default: () => ({}) },
    api: { type: Object, default: null },
    pluginId: { type: String, default: 'RssTester' },
  },
  emits: ['save', 'layout', 'switch', 'close'],
  setup(props, { emit }) {
    const source = props.initialConfig || {};
    const form = reactive({
      enabled: source.enabled !== false,
      concurrency: Number(source.concurrency) || 8,
      timeout: Number(source.timeout) || 0,
      only_active: source.only_active !== false,
      notify: source.notify === true,
      trigger: false,
    });
    const selected = ref(Array.isArray(source.site_ids) ? source.site_ids.map((item) => String(item)) : []);
    const sites = ref([]);
    const loading = ref(true);
    const loadError = ref('');
    const query = ref('');
    const saved = ref('');

    const selectedSet = computed(() => new Set(selected.value));
    const filtered = computed(() => {
      const keyword = query.value.trim().toLowerCase();
      if (!keyword) return sites.value;
      return sites.value.filter((site) => {
        const name = String(site.name || '').toLowerCase();
        const domain = String(site.domain || '').toLowerCase();
        return name.includes(keyword) || domain.includes(keyword);
      });
    });
    const selectedCount = computed(() => selected.value.length);

    const toggle = (name, checked) => {
      const current = new Set(selected.value);
      if (checked) current.add(name);
      else current.delete(name);
      selected.value = Array.from(current);
    };

    const selectAll = () => {
      const current = new Set(selected.value);
      for (const site of filtered.value) current.add(String(site.name));
      selected.value = Array.from(current);
    };

    const clearAll = () => {
      selected.value = [];
    };

    const loadSites = async () => {
      loading.value = true;
      loadError.value = '';
      if (!props.api || typeof props.api.get !== 'function') {
        loading.value = false;
        loadError.value = '宿主 API 未注入，无法读取站点列表';
        return;
      }
      try {
        const response = await props.api.get(`plugin/${props.pluginId}/sites`);
        const data = sitesPayload(response);
        if (!data) throw new Error('站点列表返回为空');
        sites.value = Array.isArray(data.sites) ? data.sites : [];
        if (!Array.isArray(source.site_ids) && Array.isArray(data.selected)) {
          selected.value = data.selected.map((item) => String(item));
        }
      } catch (exc) {
        loadError.value = errorText(exc) || '读取站点列表失败';
      } finally {
        loading.value = false;
      }
    };

    /** 读取一次检测状态，仅用于判断后台任务是否结束。 */
    const readStatus = async () => {
      if (!props.api || typeof props.api.get !== 'function') return null;
      try {
        const response = await props.api.get(`plugin/${props.pluginId}/status`);
        const candidates = [response, response && response.data];
        for (const candidate of candidates) {
          if (candidate && typeof candidate === 'object' && typeof candidate.status === 'string') {
            return candidate.status;
          }
        }
      } catch (exc) {
        return null;
      }
      return null;
    };

    let confirmTimer = null;
    let confirmTries = 0;

    const stopConfirm = () => {
      if (confirmTimer) {
        clearTimeout(confirmTimer);
        confirmTimer = null;
      }
    };

    /** 保存后等待后台检测结束，再把“立即检测一次”开关复位为关闭。 */
    const confirmTriggerReset = () => {
      stopConfirm();
      confirmTries = 0;
      const check = async () => {
        confirmTries += 1;
        const status = await readStatus();
        if (status !== 'running') {
          form.trigger = false;
          stopConfirm();
          saved.value = status === 'done' || status === 'error'
            ? '检测已结束，“立即检测一次”已自动关闭'
            : '检测请求已提交，“立即检测一次”已自动关闭';
          return;
        }
        if (confirmTries >= 60) {
          stopConfirm();
          form.trigger = false;
          saved.value = '检测仍在后台进行，可在插件页面查看进度';
          return;
        }
        confirmTimer = setTimeout(check, 2000);
      };
      confirmTimer = setTimeout(check, 1500);
    };

    const save = () => {
      if (form.trigger && !form.enabled) {
        form.trigger = false;
        saved.value = '插件未启用，本次立即检测已取消';
        return;
      }
      const trigger = !!form.trigger;
      const payload = {
        enabled: !!form.enabled,
        concurrency: Math.max(1, Math.min(16, Number(form.concurrency) || 8)),
        timeout: Math.max(0, Math.min(120, Number(form.timeout) || 0)),
        only_active: !!form.only_active,
        notify: !!form.notify,
        site_ids: selected.value.slice(),
        trigger,
      };
      emit('save', payload);
      if (trigger) {
        saved.value = '已提交，正在等待宿主确认…';
        confirmTriggerReset();
      } else {
        saved.value = '已提交保存，宿主确认后生效';
      }
    };

    onMounted(() => {
      emit('layout', { maxWidth: 720 });
      loadSites();
    });
    onUnmounted(stopConfirm);

    const field = (label, hint, control) => h('div', {
      style: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '16px',
        padding: '12px 0',
        borderBottom: '1px solid rgba(128,128,128,0.16)',
      },
    }, [
      h('div', { style: { minWidth: '0' } }, [
        h('div', { style: { fontSize: '14px' } }, label),
        hint ? h('div', { style: { fontSize: '12px', color: '#8a8a8a', marginTop: '2px' } }, hint) : null,
      ]),
      control,
    ]);

    const checkbox = (checked, onChange) => h('input', {
      type: 'checkbox',
      checked,
      onChange: (event) => onChange(!!event.target.checked),
      style: { width: '18px', height: '18px', cursor: 'pointer' },
    });

    const numberInput = (value, min, max, onChange) => h('input', {
      type: 'number',
      min,
      max,
      value,
      onInput: (event) => onChange(event.target.value),
      style: INPUT_STYLE,
    });

    const renderSiteRow = (site) => {
      const name = String(site.name || '');
      const checked = selectedSet.value.has(name);
      return h('label', {
        key: name,
        style: {
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '8px 10px',
          borderBottom: '1px solid rgba(128,128,128,0.12)',
          cursor: 'pointer',
        },
      }, [
        h('input', {
          type: 'checkbox',
          checked,
          onChange: (event) => toggle(name, !!event.target.checked),
          style: { width: '16px', height: '16px', cursor: 'pointer', flex: '0 0 auto' },
        }),
        h('div', { style: { flex: '1 1 auto', minWidth: '0' } }, [
          h('div', {
            style: {
              fontSize: '13px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            },
          }, name),
          h('div', {
            style: {
              fontSize: '11px',
              color: '#8a8a8a',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            },
          }, String(site.domain || '')),
        ]),
        site.has_rss
          ? null
          : h('span', {
            style: {
              fontSize: '11px',
              padding: '1px 7px',
              borderRadius: '9px',
              color: '#ef6c00',
              background: 'rgba(239,108,0,0.12)',
              flex: '0 0 auto',
            },
          }, '未配置 RSS'),
        site.active === false
          ? h('span', {
            style: {
              fontSize: '11px',
              padding: '1px 7px',
              borderRadius: '9px',
              color: '#666',
              background: 'rgba(128,128,128,0.16)',
              flex: '0 0 auto',
            },
          }, '已停用')
          : null,
      ]);
    };

    const renderSitePicker = () => {
      const body = [];
      body.push(h('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          paddingTop: '16px',
        },
      }, [
        h('div', [
          h('div', { style: { fontSize: '14px' } }, '检测站点'),
          h('div', { style: { fontSize: '12px', color: '#8a8a8a', marginTop: '2px' } }, '不勾选任何站点表示检测全部可检测站点'),
        ]),
        h('div', { style: { fontSize: '12px', color: '#666' } }, `已选 ${selectedCount.value} / 共 ${sites.value.length}`),
      ]));

      if (loading.value) {
        body.push(h('div', { style: { padding: '16px 0', fontSize: '13px', color: '#8a8a8a' } }, '正在读取站点列表…'));
        return body;
      }
      if (loadError.value) {
        body.push(h('div', {
          style: {
            marginTop: '10px',
            padding: '10px 12px',
            borderRadius: '8px',
            fontSize: '13px',
            color: '#c62828',
            background: 'rgba(198,40,40,0.10)',
          },
        }, loadError.value));
        body.push(h('div', { style: { marginTop: '8px' } }, [
          h('button', { type: 'button', onClick: loadSites, style: BUTTON_STYLE }, '重新读取'),
        ]));
        return body;
      }

      body.push(h('div', { style: { display: 'flex', gap: '8px', marginTop: '10px' } }, [
        h('input', {
          type: 'text',
          value: query.value,
          placeholder: '搜索站点名称或域名',
          onInput: (event) => { query.value = event.target.value; },
          style: SEARCH_STYLE,
        }),
        h('button', { type: 'button', onClick: selectAll, style: BUTTON_STYLE }, '全选'),
        h('button', { type: 'button', onClick: clearAll, style: BUTTON_STYLE }, '清空'),
      ]));

      const rows = filtered.value.map(renderSiteRow);
      body.push(h('div', {
        style: {
          marginTop: '10px',
          maxHeight: '300px',
          overflowY: 'auto',
          border: '1px solid rgba(128,128,128,0.24)',
          borderRadius: '8px',
        },
      }, rows.length > 0
        ? rows
        : [h('div', { style: { padding: '16px', fontSize: '13px', color: '#8a8a8a' } }, '没有匹配的站点')]));
      return body;
    };

    return () => h('div', { style: { padding: '8px 4px' } }, [
      h('div', { style: { fontSize: '15px', fontWeight: '600', marginBottom: '4px' } }, 'RSS 测试设置'),
      h('div', { style: { fontSize: '12px', color: '#8a8a8a', marginBottom: '8px' } }, '检测只读取站点已配置的 RSS 地址，不会修改站点本身。'),
      field('启用检测', '总开关：关闭并保存后不再发起任何检测，侧栏入口同时隐藏', checkbox(form.enabled, (value) => { form.enabled = value; })),
      field('立即检测一次', '打开并保存后立即在后台执行一轮检测，检测结束后本开关自动关闭；仅浏览插件页面不会触发检测', checkbox(form.trigger, (value) => { form.trigger = value; })),
      field('检测完成后通知', '通过已配置的通知渠道推送本轮汇总与失败站点', checkbox(form.notify, (value) => { form.notify = value; })),
      field('仅检测已启用站点', '关闭后同时检测已停用的私有站点', checkbox(form.only_active, (value) => { form.only_active = value; })),
      field('并发数', '同时检测的站点数量，范围 1-16', numberInput(form.concurrency, 1, 16, (value) => { form.concurrency = value; })),
      field('请求超时（秒）', '0 表示使用站点自身设置的超时时间', numberInput(form.timeout, 0, 120, (value) => { form.timeout = value; })),
      renderSitePicker(),
      h('div', { style: { display: 'flex', alignItems: 'center', gap: '12px', paddingTop: '16px' } }, [
        h('button', {
          type: 'button',
          onClick: save,
          style: {
            padding: '8px 18px',
            borderRadius: '8px',
            border: '1px solid rgba(128,128,128,0.4)',
            background: 'rgba(128,128,128,0.10)',
            color: 'inherit',
            cursor: 'pointer',
            fontSize: '14px',
          },
        }, '保存'),
        saved.value ? h('span', { style: { fontSize: '12px', color: '#2e7d32' } }, saved.value) : null,
      ]),
    ]);
  },
};

export default Config;