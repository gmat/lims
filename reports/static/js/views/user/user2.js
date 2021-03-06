define([
    'jquery',
    'underscore',
    'backbone',
    'iccbl_backgrid',
    'layoutmanager',
    'models/app_state',
    'views/user/user_group_permissions',
    'views/generic_detail_layout',
    'views/list2',
    'utils/tabbedController'
], function($, _, Backbone, Iccbl, layoutmanager, 
            appModel, UserGroupPermissionView, DetailLayout, 
            ListView, TabbedController) {

  var UserView = TabbedController.extend({

    initialize: function(args) {
      var self = this;
      this._classname = 'userView';

      this.title = Iccbl.getTitleFromTitleAttribute(this.model, this.model.resource);

      TabbedController.prototype.initialize.apply(this,arguments);
    },
    
    tabbed_resources: {
        detail: { 
          description: 'User Details', 
          title: 'User Details', 
          invoke: 'setDetail' },
        usergrouppermissions: { 
          description: 'User Groups and Permissions', 
          title: 'User Groups and Permissions', 
          invoke: 'setGroupsPermissions'
        },
    },
    
    /**
     * Layoutmanager hook
     */
    serialize: function() {
      var self = this;
      return {
        'base_url': self.model.resource.key + '/' + self.model.key,
        'tab_resources': this.tabbed_resources
      }      
    }, 
    
    setDetail: function(delegateStack){
      var key = 'detail';
 
      var view = new DetailLayout({ 
        model: this.model, 
        uriStack: delegateStack
      });
      view.showEdit = function(){
        appModel.initializeAdminMode(function(){
          
          self.model.resource.fields['permissions']['choices'] = 
            appModel.getPermissionsOptions();
          self.model.resource.fields['usergroups']['choices'] = 
            appModel.getUserGroupOptions();
          DetailLayout.prototype.showEdit.call(view,arguments);
        });
      };
      
      // NOTE: have to re-listen after removing a view
      this.listenTo(view , 'uriStack:change', this.reportUriStack);
      // Note: since detail_layout reports the tab, the consumedStack is empty here
      this.consumedStack = []; 
      this.setView("#tab_container", view ).render();
      return view;
    },
    
    setGroupsPermissions(delegateStack){
      var self = this;

      var pUserModel = this.model.clone();
      pUserModel.key = this.model.key;
      var resource = appModel.getResource(this.model.resource.key);
//      var resource = _.extend({},this.model.resource);
//      resource = _.extend({}, this.model.resource );
      resource.fields = _.pick(
        this.model.resource.fields,
        ['username','first_name','last_name','usergroups','permissions']);
      resource.fields['first_name']['visibility'] = [];
      resource.fields['last_name']['visibility'] = [];
      resource.fields['usergroups']['visibility'] = [];
      resource.fields['usergroups']['editability'] = ['u'];
      resource.fields['permissions']['editability'] = ['u'];
      resource.fields['first_name']['editability'] = [];
      resource.fields['last_name']['editability'] = [];
      pUserModel.resource = resource;
      pUserModel.resource.fields['permissions']['choices'] = (
          appModel.get('permissionOptions'));
      
      var view = new DetailLayout({ 
        model: pUserModel, 
        buttons: ['history'],
        uriStack: delegateStack,
        DetailView: UserGroupPermissionView
      });
      
      Backbone.Layout.setupView(view);
      this.consumedStack = ['usergrouppermissions']; 
      self.listenTo(view , 'uriStack:change', self.reportUriStack);
      this.setView("#tab_container", view ).render();
    },
    onClose: function() {
      // TODO: is this necessary when using Backbone LayoutManager
      this.tabViews = {};
      this.remove();
    }

  });

  return UserView;
});