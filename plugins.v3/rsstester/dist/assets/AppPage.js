import { importShared } from './__federation_fn_import-054b33c3.js';

const vueNs = (await importShared('vue')) || {};
const V = typeof vueNs.h === 'function'
  ? vueNs
  : (vueNs.default && typeof vueNs.default.h === 'function' ? vueNs.default : (typeof vueNs === 'function' ? vueNs : {}));
const h = V.h;
const ref = V.ref;
const computed = V.computed;
const onMounted = V.onMounted;
const onUnmounted = typeof V.onUnmounted === 'function' ? V.onUnmounted : () => {};

const POLL_INTERVAL = 1200;

const STATUS_META = {
  valid: { label: '有效', color: '#2e7d32', bg: 'rgba(46,125,50,0.12)' },
  expired: { label: '已过期', color: '#ef6c00', bg: 'rgba(239,108,0,0.12)' },
  failed: { label: '失败', color: '#c62828', bg: 'rgba(198,40,40,0.12)' },
};

const CARD_STYLE = {
  border: '1px solid rgba(128,128,128,0.24)',
  borderRadius: '10px',
  background: 'rgba(128,128,128,0.04)',
  overflow: 'hidden',
};

const STAT_STYLE = {
  flex: '1 1 120px',
  minWidth: '110px',
  padding: '12px 14px',
  border: '1px solid rgba(128,128,128,0.24)',
  borderRadius: '10px',
  background: 'rgba(128,128,128,0.04)',
};

const ROW_STYLE = {
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  padding: '10px 14px',
  borderBottom: '1px solid rgba(128,128,128,0.16)',
};

const MUTED = { fontSize: '12px', color: '#8a8a8a' };

const number = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

/** 兼容宿主 api 的多种响应包装，只取真正的业务负载。 */
const unwrapPayload = (response) => {
  if (!response || typeof response !== 'object') return null;
  const candidates = [response, response.data];
  for (const candidate of candidates) {
    if (candidate && typeof candidate === 'object'
      && (candidate.status !== undefined || Array.isArray(candidate.items))) {
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

const AppPage = {
  name: 'RssTesterAppPage',
  props: {
    api: { type: Object, default: null },
    pluginId: { type: String, default: 'RssTester' },
    navKey: { type: String, default: 'main' },
  },
  setup(props) {
    const payload = ref(null);
    const error = ref('');
    const requesting = ref(false);
    const showSettings = ref(false);
    const settingsConfig = ref(null);
    const settingsLoading = ref(false);
    const settingsError = ref('');
    const settingsSaved = ref('');
    let ConfigComponent = null;
    let timer = null;
    let disposed = false;

    const items = computed(() => (payload.value && Array.isArray(payload.value.items) ? payload.value.items : []));
    const status = computed(() => (payload.value && payload.value.status) || 'idle');
    const running = computed(() => status.value === 'running');
    const disabled = computed(() => status.value === 'disabled');
    const scopeText = computed(() => {
      const config = (payload.value && payload.value.config) || {};
      const chosen = Array.isArray(config.site_ids) ? config.site_ids : [];
      return chosen.length > 0 ? `检测范围：已选择的 ${chosen.length} 个站点` : '检测范围：全部可检测站点';
    });
    const total = computed(() => number(payload.value && payload.value.total));
    const done = computed(() => number(payload.value && payload.value.done));
    const percent = computed(() => (total.value > 0 ? Math.min(100, Math.round((done.value / total.value) * 100)) : 0));

    const stopPolling = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    /** 读取一次快照：action 为 status 时只读，check 时才会真正发起检测。 */
    const fetchState = async (action) => {
      timer = null;
      if (disposed) return;
      if (!props.api || typeof props.api.get !== 'function') {
        error.value = '宿主 API 未注入，无法检测';
        requesting.value = false;
        return;
      }
      try {
        const response = await props.api.get(`plugin/${props.pluginId}/${action}`);
        const data = unwrapPayload(response);
        if (!data) throw new Error('接口返回为空');
        payload.value = data;
        error.value = data.status === 'error' ? (data.error || '检测失败') : '';
        if (data.status === 'running') {
          requesting.value = true;
          timer = setTimeout(() => fetchState('status'), POLL_INTERVAL);
        } else {
          requesting.value = false;
        }
      } catch (exc) {
        error.value = errorText(exc) || '检测请求失败';
        requesting.value = false;
      }
    };

    /** 打开页面只读取已有结果，不自动发起检测。 */
    const refresh = async () => {
      stopPolling();
      await fetchState('status');
    };

    const run = async () => {
      if (disabled.value) return;
      if (running.value || requesting.value) return;
      stopPolling();
      error.value = '';
      requesting.value = true;
      await fetchState('check');
    };

    /** 懒加载本插件自带的设置组件，避免打开页面就加载整份配置表单。 */
    const loadConfigComponent = async () => {
      if (ConfigComponent) return ConfigComponent;
      const mod = await import('./Config.js?v=1.0.6');
      ConfigComponent = (mod && (mod.default || mod)) || null;
      return ConfigComponent;
    };

    /** 在页面内展开本插件的设置面板，读取当前配置后交给 Config 组件渲染。 */
    const openSettings = async () => {
      settingsSaved.value = '';
      settingsError.value = '';
      showSettings.value = true;
      settingsLoading.value = true;
      try {
        const readConfig = props.api && typeof props.api.get === 'function'
          ? props.api.get(`plugin/${props.pluginId}`)
          : Promise.resolve({});
        const [component, response] = await Promise.all([loadConfigComponent(), readConfig]);
        if (!component) throw new Error('设置组件加载失败');
        const wrapper = response && response.data && typeof response.data === 'object' ? response.data : response;
        const raw = wrapper && typeof wrapper === 'object' && wrapper.conf && typeof wrapper.conf === 'object'
          ? wrapper.conf
          : wrapper;
        settingsConfig.value = raw && typeof raw === 'object' ? raw : {};
      } catch (exc) {
        settingsError.value = errorText(exc) || '打开设置失败';
      } finally {
        settingsLoading.value = false;
      }
    };

    /** 设置面板保存：提交完整配置对象，并同步刷新页面状态。 */
    const saveSettings = async (next) => {
      settingsSaved.value = '';
      settingsError.value = '';
      if (!props.api || typeof props.api.put !== 'function') {
        settingsError.value = '宿主 API 不支持保存设置';
        return;
      }
      try {
        const response = await props.api.put(`plugin/${props.pluginId}`, next);
        const wrapper = response && response.data ? response.data : response;
        if (wrapper && typeof wrapper === 'object' && wrapper.success === false) {
          throw new Error(wrapper.message || '保存失败');
        }
        settingsSaved.value = '设置已保存';
        settingsConfig.value = { ...(settingsConfig.value || {}), ...(next || {}) };
        await refresh();
      } catch (exc) {
        settingsError.value = errorText(exc) || '保存设置失败';
      }
    };

    onMounted(refresh);
    onUnmounted(() => {
      disposed = true;
      stopPolling();
    });

    const renderStat = (label, value, color) => h('div', { style: STAT_STYLE }, [
      h('div', { style: { fontSize: '12px', color: '#8a8a8a' } }, label),
      h('div', { style: { fontSize: '22px', fontWeight: '700', lineHeight: '1.3', color: color || 'inherit' } }, String(number(value))),
    ]);

    const renderRow = (item) => {
      const meta = STATUS_META[item.status] || STATUS_META.failed;
      return h('div', { style: ROW_STYLE }, [
        h('span', { style: { width: '8px', height: '8px', borderRadius: '50%', flex: '0 0 auto', background: meta.color } }),
        h('div', { style: { flex: '1 1 auto', minWidth: '0' } }, [
          h('div', { style: { fontSize: '14px', fontWeight: '600', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, item.name || '未知站点'),
          h('div', { style: { fontSize: '12px', color: '#8a8a8a', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, item.message || item.domain || ''),
        ]),
        h('span', { style: { fontSize: '12px', color: '#666', flex: '0 0 auto' } }, `${number(item.count)} 条`),
        h('span', { style: { fontSize: '12px', color: '#666', flex: '0 0 auto', minWidth: '58px', textAlign: 'right' } }, `${number(item.elapsed_ms)} ms`),
        h('span', { style: { fontSize: '12px', flex: '0 0 auto', padding: '2px 8px', borderRadius: '10px', color: meta.color, background: meta.bg } }, meta.label),
      ]);
    };

    return () => {
      const body = [];
      const busy = running.value || requesting.value || disabled.value;

      body.push(h('div', { style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' } }, [
        h('div', { style: { fontSize: '20px', fontWeight: '700' } }, 'RSS 测试'),
        h('button', {
          type: 'button',
          disabled: busy,
          onClick: run,
          style: {
            padding: '6px 14px',
            borderRadius: '8px',
            border: '1px solid rgba(128,128,128,0.4)',
            background: busy ? 'rgba(128,128,128,0.16)' : 'transparent',
            color: 'inherit',
            cursor: busy ? 'not-allowed' : 'pointer',
            fontSize: '13px',
          },
        }, running.value || requesting.value ? '检测中…' : (status.value === 'done' ? '重新检测' : '开始检测')),
        h('button', {
          type: 'button',
          onClick: openSettings,
          title: '在页面内打开本插件设置',
          style: {
            padding: '6px 14px',
            borderRadius: '8px',
            border: '1px solid rgba(128,128,128,0.4)',
            background: 'transparent',
            color: 'inherit',
            cursor: 'pointer',
            fontSize: '13px',
          },
        }, '设置'),
        payload.value
          ? h('div', { style: MUTED }, status.value === 'running'
            ? `已完成 ${done.value}/${total.value || '?'}`
            : `检测时间 ${payload.value.checked_at || '-'}，总耗时 ${number(payload.value.elapsed_ms)} ms`)
          : null,
        payload.value && !disabled.value
          ? h('div', { style: { ...MUTED, color: '#1976d2' } }, scopeText.value)
          : null,
      ]));

      if (showSettings.value) {
        const panel = [
          h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' } }, [
            h('div', { style: { fontSize: '16px', fontWeight: '600' } }, '插件设置'),
            h('button', {
              type: 'button',
              onClick: () => { showSettings.value = false; },
              style: {
                padding: '4px 12px',
                borderRadius: '8px',
                border: '1px solid rgba(128,128,128,0.4)',
                background: 'transparent',
                color: 'inherit',
                cursor: 'pointer',
                fontSize: '13px',
              },
            }, '关闭'),
          ]),
        ];
        if (settingsError.value) {
          panel.push(h('div', { style: { marginTop: '8px', padding: '8px 12px', borderRadius: '8px', fontSize: '13px', color: '#c62828', background: 'rgba(198,40,40,0.10)' } }, settingsError.value));
        }
        if (settingsSaved.value) {
          panel.push(h('div', { style: { marginTop: '8px', padding: '8px 12px', borderRadius: '8px', fontSize: '13px', color: '#2e7d32', background: 'rgba(46,125,50,0.12)' } }, settingsSaved.value));
        }
        if (settingsLoading.value) {
          panel.push(h('div', { style: { padding: '16px 0', fontSize: '13px', color: '#8a8a8a' } }, '正在加载设置…'));
        } else if (settingsConfig.value && ConfigComponent) {
          panel.push(h(ConfigComponent, {
            initialConfig: settingsConfig.value,
            api: props.api,
            pluginId: props.pluginId,
            onSave: saveSettings,
          }));
        } else if (!settingsError.value) {
          panel.push(h('div', { style: { padding: '16px 0', fontSize: '13px', color: '#8a8a8a' } }, '设置组件未就绪'));
        }
        body.push(h('div', {
          style: {
            marginBottom: '16px',
            padding: '16px',
            borderRadius: '10px',
            border: '1px solid rgba(128,128,128,0.24)',
            background: 'rgba(128,128,128,0.04)',
          },
        }, panel));
      }

      if (disabled.value) {
        body.push(h('div', {
          style: {
            marginBottom: '14px',
            padding: '12px 14px',
            borderRadius: '8px',
            fontSize: '13px',
            color: '#ef6c00',
            background: 'rgba(239,108,0,0.12)',
          },
        }, (payload.value && payload.value.error) || '插件未启用：请在插件设置中开启“启用检测”并保存后再检测。'));
      }

      if (status.value === 'idle' && !disabled.value) {
        body.push(h('div', { style: { marginBottom: '14px', padding: '20px', borderRadius: '10px', border: '1px dashed rgba(128,128,128,0.32)', fontSize: '13px', color: '#8a8a8a', lineHeight: '1.7' } }, [
          h('div', { style: { color: 'inherit' } }, '打开本页面不会自动开始检测。'),
          h('div', {}, '可在插件设置中打开“立即检测一次”并保存，或直接点击上方“开始检测”。'),
        ]));
      }

      if (running.value) {
        body.push(h('div', { style: { marginBottom: '16px' } }, [
          h('div', { style: { height: '6px', borderRadius: '3px', background: 'rgba(128,128,128,0.18)', overflow: 'hidden' } }, [
            h('div', { style: { width: `${percent.value}%`, height: '100%', background: '#1976d2', transition: 'width 0.3s' } }),
          ]),
          h('div', { style: { marginTop: '6px', fontSize: '12px', color: '#8a8a8a' } }, total.value > 0
            ? `正在检测站点 RSS：${percent.value}%（${done.value}/${total.value}）`
            : '正在读取站点列表…'),
        ]));
      }

      if (error.value) {
        body.push(h('div', { style: { marginBottom: '14px', padding: '10px 14px', borderRadius: '8px', fontSize: '13px', color: '#c62828', background: 'rgba(198,40,40,0.10)' } }, error.value));
      }

      if (payload.value && (status.value === 'done' || payload.value.total > 0)) {
        body.push(h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' } }, [
          renderStat('站点总数', payload.value.total),
          renderStat('有效', payload.value.valid, '#2e7d32'),
          renderStat('已过期', payload.value.expired, '#ef6c00'),
          renderStat('失败', payload.value.failed, '#c62828'),
        ]));
      }

      if (items.value.length > 0) {
        body.push(h('div', { style: CARD_STYLE }, items.value.map(renderRow)));
      } else if (status.value === 'done') {
        body.push(h('div', { style: { padding: '24px', textAlign: 'center', fontSize: '13px', color: '#8a8a8a' } }, '没有可检测的站点：请检查设置中的站点选择与“仅检测已启用站点”开关。'));
      }

      return h('div', { style: { padding: '16px', maxWidth: '1080px', margin: '0 auto' } }, body);
    };
  },
};

export default AppPage;