import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach } from 'vitest'
const mocks=vi.hoisted(()=>({get:vi.fn(),post:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>({query:{}})}))
vi.mock('./api',async(importOriginal)=>({...await importOriginal<typeof import('./api')>(),getAdminApi:()=>mocks}))
import AdminUsers from './AdminUsers.vue'
import AdminCourses from './AdminCourses.vue'
const global={stubs:{AdminClassDialog:true,SiteDialog:{props:['open'],template:'<section v-if="open"><slot/><slot name="footer"/></section>'}}}
describe('admin mutations',()=>{
 beforeEach(()=>{vi.clearAllMocks();mocks.get.mockResolvedValue({items:[]});mocks.post.mockResolvedValue({})})
 it('keeps password reset failure visible without claiming success',async()=>{mocks.get.mockResolvedValue({items:[{id:7,name:'教师甲',username:'teacher',role:'teacher',is_active:true}]});mocks.post.mockRejectedValue(new Error('当前账号不能操作'));const w=mount(AdminUsers,{global});await flushPromises();await w.findAll('button').find(b=>b.text()==='重置密码')!.trigger('click');expect(w.find('input[type="password"]').exists()).toBe(false);expect(w.text()).toContain('S + 登录账号');await w.get('form').trigger('submit');await flushPromises();expect(mocks.post).toHaveBeenCalledWith('users/7/password/',{confirmation:'reset'});expect(w.get('[role="alert"]').text()).toBe('当前账号不能操作');expect(w.text()).not.toContain('账号变更已保存。')})
 it.each(['teacher','student','admin'])('creates %s accounts without sending a password',async(role)=>{
  mocks.get.mockImplementation((path:string)=>Promise.resolve({items:path.startsWith('choices/courses/')?[{value:1,label:'课程'}]:path.startsWith('choices/classes/')?[{value:2,label:'班级'}]:[],total:1,page:1,total_pages:1}))
  const w=mount(AdminUsers,{global});await flushPromises()
  const label={teacher:'教师',student:'学生',admin:'管理员'}[role]
  await w.findAll('.tabs button').find(b=>b.text().startsWith(label!))!.trigger('click')
  await w.findAll('button').find(b=>b.text().includes('新增'+label))!.trigger('click')
  expect(w.find('input[type="password"]').exists()).toBe(false)
  expect(w.text()).toContain(role==='student'?'S + 学号':'S + 登录账号')
  expect(w.text()).toContain('登录后提醒修改')
  await w.get('input[name="name"]').setValue('测试账号')
  await w.get(role==='student'?'input[name="student_number"]':'input[name="username"]').setValue(role==='student'?'20260001':'teacher.account')
  if(role!=='student')expect(w.get('input[name="username"]').element.closest('label')?.textContent).toBe('登录账号')
  if(role==='student'){await w.findAll('[role="combobox"]')[0]!.trigger('click');await flushPromises();await w.get('[role="option"]').trigger('click');await w.findAll('[role="combobox"]')[1]!.trigger('click');await flushPromises();await w.get('[role="option"]').trigger('click')}
  await w.get('form').trigger('submit');await flushPromises()
  if(role==='student')expect(mocks.post).toHaveBeenCalledWith('users/',expect.objectContaining({course_id:1,class_group_id:2}))
  expect(mocks.post).toHaveBeenCalledWith('users/',expect.objectContaining({role}))
  expect(mocks.post.mock.calls[0]![1]).not.toHaveProperty('password')
 })
 it('sends the server concurrency token when editing a course',async()=>{mocks.get.mockImplementation((path:string)=>Promise.resolve({items:path==='courses/'?[{id:9,name:'课程甲',code:'A',term:'2026',owner_id:2,state_token:'original-token'}]:[]}));const w=mount(AdminCourses,{global});await flushPromises();await w.findAll('button').find(b=>b.text()==='管理课程')!.trigger('click');await w.findAll('button').find(b=>b.text()==='编辑基础信息')!.trigger('click');await w.get('form').trigger('submit');await flushPromises();expect(mocks.post).toHaveBeenCalledWith('courses/9/update/',expect.objectContaining({state_token:'original-token',name:'课程甲'}))})
 it('requires a typed course name and confirms the course recycle retention',async()=>{
  mocks.get.mockImplementation((path:string)=>Promise.resolve({items:path==='courses/'?[{id:9,name:'课程甲',code:'A',term:'2026'}]:[]}))
  mocks.post.mockImplementation((path:string)=>Promise.resolve(path.endsWith('delete-preview/')?{allowed:true,counts:{classes:2,sessions:3},confirmation_token:'preview-token'}:{deleted:true,recoverable:true,recycle_id:9,operation_id:'9',restore_until:'2026-11-16T00:00:00Z'}))
  const w=mount(AdminCourses,{global});await flushPromises()
  await w.findAll('button').find(b=>b.text()==='管理课程')!.trigger('click')
  await w.findAll('button').find(b=>b.text()==='查看删除影响')!.trigger('click');await flushPromises()
  const confirm=w.findAll('button').find(b=>b.text()==='确认删除')!
  expect(confirm.attributes('disabled')).toBeDefined()
  await w.get('input[name="confirmation_name"]').setValue('课程甲')
  await confirm.trigger('click');await flushPromises()
  expect(mocks.post).toHaveBeenLastCalledWith('courses/9/delete/',{confirmation:'DELETE',confirmation_name:'课程甲',confirmation_token:'preview-token'})
  expect(w.get('[role="status"]').text()).toContain('14 天内可恢复')
  expect(w.get('[role="status"]').text()).toContain('回收站')
 })

})
