export interface SiteSelectOption {value:string|number;label:string;description?:string;disabled?:boolean}
export interface SiteSelectPage {items:SiteSelectOption[];total:number;page:number;total_pages:number;selected?:SiteSelectOption}
