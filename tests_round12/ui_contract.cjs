'use strict';
// Headless source-level React contract. It exercises compiled TS event handlers
// against mocked HTTP responses; it does NOT claim real Chromium rendering.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const app=path.resolve(__dirname,'../web/react/app.js');
let instance;const calls=[];
class ReactComponent {
  constructor(props){this.props=props;this.state={};}
  setState(p){Object.assign(this.state,p);}
}
const react={Component:ReactComponent,createElement:(tag,props,...children)=>({tag,props:props||{},children})};
const reactDOM={render(node){instance=new node.tag(node.props);}};
const fixtures={
  '/api/services':[{'name':'demo','desired_version':'v1','generation':1,'active_version':'v1',
     'active_generation':1,'active_endpoint':'http://127.0.0.1:8766'}],
  '/api/audit':[{'id':1,'ts':'2026-10-16','service':'demo','generation':1,'event':'PROMOTED','detail':'ok'}],
  '/api/status/demo':{'service':{'name':'demo','active_endpoint':'http://127.0.0.1:8766'},
    'jobs':[{'id':1,'state':'PROMOTED','attempts':1,'generation':1}],
    'effects':[{'effect_id':'release:demo:1','state':'ACKED','attempts':1,'generation':1}]},
  '/api/deploy':{'service':'demo','generation':1,'status':'PENDING'}
};
const sandbox={React:react,ReactDOM:reactDOM,document:{getElementById:()=>({})},
  setInterval:()=>0,clearInterval:()=>{},Date,Math,encodeURIComponent,
  fetch:async(url,opt={})=>{calls.push({url,opt});return {ok:true,status:200,text:async()=>JSON.stringify(fixtures[url])};},
  console};
vm.runInNewContext(fs.readFileSync(app,'utf8'),sandbox,{filename:'app.js'});
function find(node,predicate,result=[]){
  if(node && typeof node==='object'){
    if(predicate(node))result.push(node);
    for(const child of (node.children||[]))find(child,predicate,result);
  }
  return result;
}
async function run(){
  assert(instance,'ReactDOM.render should mount the React component');
  assert(find(instance.render(),x=>x.tag==='h1').some(x=>x.children.includes('Deployment Control Plane')));
  const buttons=find(instance.render(),x=>x.tag==='button');
  const deploy=buttons.find(x=>x.children.includes('提交发布'));
  const refresh=buttons.find(x=>x.children.includes('刷新状态'));
  assert(deploy && refresh,'UI must expose release and status actions');
  await deploy.props.onClick();
  assert(instance.state.error.includes('请先输入管理令牌'));
  instance.setState({token:'local-admin',service:'demo',version:'v1'});
  await deploy.props.onClick();
  assert(calls.some(c=>c.url==='/api/deploy' && c.opt.method==='POST'
    && c.opt.headers['X-Admin-Token']==='local-admin'
    && JSON.parse(c.opt.body).service==='demo'));
  assert(instance.state.services.length===1);
  assert(instance.state.audit[0].event==='PROMOTED');
  assert(instance.state.detail.effects[0].state==='ACKED');
  assert(find(instance.render(),x=>x.tag==='td').length>=4);
  await refresh.props.onClick();
  assert(calls.some(c=>c.url==='/api/status/demo'));
  console.log('REACT_TS_API_EVENT_CONTRACT=PASS; calls='+calls.length);
}
run().catch(err=>{console.error(err);process.exitCode=1;});
