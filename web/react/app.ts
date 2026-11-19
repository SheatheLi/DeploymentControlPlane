/* TypeScript UI executed by the real, locally bundled React/ReactDOM UMD libs.
   Explicit typed API contracts; token is ephemeral and never stored in browser storage. */
declare const React: any;
declare const ReactDOM: any;

type Release = { name:string;desired_version:string;generation:number;
  active_version:string|null;active_generation:number|null;active_endpoint:string|null };
type Audit = {id:number;ts:string;service:string;generation:number;event:string;detail:string};
type Detail = {service:Release;jobs:Array<{id:number;state:string;attempts:number;generation:number}>;
  effects:Array<{effect_id:string;state:string;attempts:number;generation:number}>};

type PageState={token:string;service:string;version:string;key:string;
  busy:boolean;error:string;message:string;services:Release[];audit:Audit[];detail:Detail|null};
const el = (tag:any, props:any, ...children:any[])=>React.createElement(tag,props,...children);
const badge = (value:string|null|undefined)=>el('span',{className:'state'},value||'—');

class Dashboard extends React.Component {
  state:PageState={token:'',service:'demo',version:'v1',key:'',busy:false,error:'',message:'',
    services:[],audit:[],detail:null};
  private clock:any=null;
  componentDidMount(){this.clock=setInterval(()=>{if(this.state.token && !this.state.busy)this.refresh(true)},4000);}
  componentWillUnmount(){clearInterval(this.clock);}

  private async api(path:string, method='GET', body?:object):Promise<any>{
    const headers:any={'X-Admin-Token':this.state.token};
    if(body)headers['Content-Type']='application/json';
    const res=await fetch(path,{method,headers,body:body?JSON.stringify(body):undefined});
    const raw=await res.text();
    let value:any={};try{value=raw?JSON.parse(raw):{}}catch{value={detail:raw};}
    if(!res.ok)throw new Error(value.detail||('HTTP '+res.status));
    return value;
  }
  private async refresh(silent=false){
    if(!this.state.token){if(!silent)this.setState({error:'输入管理令牌后才能读取状态'});return;}
    if(!silent)this.setState({busy:true,error:''});
    try {
      const [services,audit]=await Promise.all([this.api('/api/services'),this.api('/api/audit')]);
      let detail:Detail|null=null;
      if(this.state.service && services.some((x:Release)=>x.name===this.state.service)){
        detail=await this.api('/api/status/'+encodeURIComponent(this.state.service));
      }
      this.setState({services,audit,detail,error:''});
    }catch(e){this.setState({error:String(e)});}
    finally{if(!silent)this.setState({busy:false});}
  }
  private async deploy(){
    if(!this.state.token){this.setState({error:'请先输入管理令牌'});return;}
    const key=this.state.key.trim() || ('ui-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2));
    this.setState({busy:true,error:'',message:''});
    try{
      const result=await this.api('/api/deploy','POST',{
        service:this.state.service.trim(),version:this.state.version.trim(),idempotency_key:key});
      this.setState({message:`已持久化请求：Generation ${result.generation}。独立 Worker 负责后续发布。`,key});
      await this.refresh(true);
    }catch(e){this.setState({error:String(e)});}
    finally{this.setState({busy:false});}
  }
  private field(label:string,value:string,onChange:(x:string)=>void,type='text'){
    return el('label',{className:'field'},el('span',{},label),el('input',{
      type,value,onChange:(evt:any)=>onChange(evt.target.value),autoComplete:'off'}));
  }
  render(){const s:PageState=this.state;
    return el('div',{className:'shell'},
      el('header',{className:'header'},
        el('div',{},el('p',{className:'eyebrow'},'DURABLE DELIVERY / RELEASE CONTROL'),
          el('h1',{},'Deployment Control Plane'),
          el('p',{className:'lead'},'期望状态、健康门控、租约保护、事务 Outbox 与副作用回执')),
        el('div',{className:'environment'},el('strong',{},'PostgreSQL 模式'),
          el('small',{},'API 与 Worker 由不同进程运行'))),
      el('div',{className:'columns'},
        el('section',{className:'card form'},el('h2',{},'申请发布'),
          el('p',{className:'hint'},'管理令牌只保留在当前页面内存，不写入本地存储。'),
          this.field('管理令牌',s.token,x=>this.setState({token:x}),'password'),
          this.field('服务名称',s.service,x=>this.setState({service:x})),
          this.field('目标版本',s.version,x=>this.setState({version:x})),
          this.field('幂等键（留空则自动生成）',s.key,x=>this.setState({key:x})),
          el('div',{className:'actions'},
            el('button',{disabled:s.busy,onClick:()=>this.deploy()},s.busy?'处理中…':'提交发布'),
            el('button',{className:'secondary',disabled:s.busy,onClick:()=>this.refresh()},'刷新状态')),
          s.message&&el('p',{role:'status',className:'success'},s.message),
          s.error&&el('p',{role:'alert',className:'error'},s.error)),
        el('section',{className:'card'},el('div',{className:'sectionhead'},el('h2',{},'服务现状'),
          el('small',{},'期望 / 生效版本')),
          s.services.length?el('table',{},el('thead',{},el('tr',{},
            ...['服务','期望版本','生效版本','代际'].map(t=>el('th',{key:t},t)))),
            el('tbody',{},...s.services.map(x=>el('tr',{key:x.name},
              el('td',{},x.name),el('td',{},badge(x.desired_version)),
              el('td',{},badge(x.active_version)),
              el('td',{},`${x.active_generation??'—'} / ${x.generation}`)))))
            :el('div',{className:'empty'},'暂无服务记录；输入令牌后点击刷新'))),
      el('div',{className:'columns'},
        el('section',{className:'card'},el('h2',{},'执行与副作用'),
          s.detail?el('div',{},
            el('p',{className:'hint'},`服务 ${s.detail.service.name} · active endpoint: ${s.detail.service.active_endpoint||'—'}`),
            el('h3',{},'发布任务'),
            ...s.detail.jobs.map(j=>el('div',{key:'job'+j.id,className:'event'},
              el('strong',{},`#${j.id} ${j.state}`),el('span',{},`G${j.generation} · 尝试 ${j.attempts}`))),
            el('h3',{},'Outbox'),
            ...s.detail.effects.map(e=>el('div',{key:e.effect_id,className:'event'},
              el('strong',{},e.state),el('span',{},`${e.effect_id} · ${e.attempts} 次`))))
            :el('p',{className:'empty'},'选择已有服务以查看任务与 Outbox')),
        el('section',{className:'card'},el('h2',{},'最近审计'),
          s.audit.length?el('div',{className:'log'},...s.audit.slice(0,20).map(a=>el('div',{className:'event',key:a.id},
            el('strong',{},`${a.event} · ${a.service}`),
            el('span',{},`G${a.generation} · ${a.detail}`))))
          :el('p',{className:'empty'},'等待审计记录'))),
      el('footer',{},'本地研发控制台 · 不代表 Kubernetes、容器或生产发布验收')
    );
  }
}
ReactDOM.render(el(Dashboard,{}),document.getElementById('root'));
