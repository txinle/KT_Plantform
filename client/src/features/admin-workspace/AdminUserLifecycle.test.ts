import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,describe,expect,it,vi} from 'vitest'
const mocks=vi.hoisted(()=>({get:vi.fn(),post:vi.fn()}))
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),getAdminApi:()=>mocks}))
import AdminUsers from './AdminUsers.vue'
import AdminRecycleDialog from './AdminRecycleDialog.vue'
const global={stubs:{SiteDialog:{props:['open'],template:'<section v-if="open"><slot/><slot name="footer"/></section>'}}}
describe('account enrollment and recycling',()=>{
 beforeEach(()=>{vi.clearAllMocks();mocks.post.mockResolvedValue({})})
 it('loads server course and class choices and clears class on course change',async()=>{
  mocks.get.mockImplementation((path:string)=>Promise.resolve({items:path.startsWith('choices/courses/')?[{value:1,label:'甲'},{value:2,label:'乙'}]:path.startsWith('choices/classes/')?[{value:10,label:'一班'}]:[],total:2,page:1,total_pages:1}))
  const w=mount(AdminUsers,{global});await flushPromises();await w.findAll('.tabs button')[1]!.trigger('click');await w.findAll('button').find(b=>b.text().includes('新增学生'))!.trigger('click');await flushPromises()
  expect(w.text()).toContain('初始密码为 S + 学号，首次登录后提醒修改。')
  await w.get('form').trigger('submit');expect(mocks.post).not.toHaveBeenCalled()
  expect(w.findAll('[role="combobox"]')[1]!.attributes('disabled')).toBeDefined()
  await w.findAll('[role="combobox"]')[0]!.trigger('click');await flushPromises();await w.findAll('[role="option"]')[0]!.trigger('click')
  await w.findAll('[role="combobox"]')[1]!.trigger('click');await flushPromises();expect(mocks.get).toHaveBeenLastCalledWith('choices/classes/?q=&page=1&course_id=1');await w.get('[role="option"]').trigger('click')
  expect((w.get('input[name="class_group_id"]').element as HTMLInputElement).value).toBe('10')
  await w.findAll('[role="combobox"]')[0]!.trigger('click');await flushPromises();await w.findAll('[role="option"]')[1]!.trigger('click')
  expect((w.get('input[name="class_group_id"]').element as HTMLInputElement).value).toBe('0')
  expect(w.findAll('[role="combobox"]')[1]!.text()).not.toContain('一班')
  expect(mocks.get.mock.calls.some(([path])=>path==='courses/'||path==='classes/')).toBe(false)
  expect(mocks.get.mock.calls.some(([path])=>/selected_id=(&|$)/.test(path))).toBe(false)
 })
 it('requires server preview and typed name before deleting an account',async()=>{mocks.get.mockResolvedValue({items:[{id:7,name:'教师甲',role:'teacher',is_active:true}]});mocks.post.mockImplementation((p:string)=>Promise.resolve(p.endsWith('delete-preview/')?{name:'教师甲',allowed:true,counts:{courses:2},confirmation_token:'proof',retention_days:14}:{}));const w=mount(AdminUsers,{global});await flushPromises();await w.findAll('button').find(b=>b.text()==='删除')!.trigger('click');await flushPromises();const confirm=w.findAll('button').find(b=>b.text()==='确认删除')!;expect(confirm.attributes('disabled')).toBeDefined();await w.get('input[name="confirmation_name"]').setValue('教师甲');await w.get('form').trigger('submit');await flushPromises();expect(mocks.post).toHaveBeenLastCalledWith('users/7/delete/',{confirmation:'DELETE',confirmation_name:'教师甲',confirmation_token:'proof'});expect(w.get('tbody').text()).not.toContain('教师甲');expect(w.get('[role="status"]').text()).toContain('14 天')})
 it('filters and paginates recycle entries, confirms restore and disables expired entries',async()=>{mocks.get.mockResolvedValue({items:[...Array.from({length:21},(_,i)=>({id:i+1,kind:'user',name:`账号${i+1}`,can_restore:i!==0,counts:{},restore_until:'2026-11-16'})),{id:88,kind:'course',name:'课程不得混入',can_restore:true}]});const w=mount(AdminRecycleDialog,{props:{open:true,kind:'user'},global});await flushPromises();expect(w.findAll('tbody tr')).toHaveLength(20);expect(w.text()).not.toContain('课程不得混入');expect(w.findAll('button').find(b=>b.text()==='恢复')!.attributes('disabled')).toBeDefined();await w.get('[data-next]').trigger('click');await w.findAll('button').find(b=>b.text()==='恢复')!.trigger('click');expect(mocks.post).not.toHaveBeenCalled();await w.findAll('button').find(b=>b.text()==='确认恢复')!.trigger('click');await flushPromises();expect(mocks.post).toHaveBeenCalledWith('recycle/21/restore/',{});expect(w.emitted('changed')).toBeTruthy()})
})
