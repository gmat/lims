define([
  'jquery',
  'underscore',
  'backbone',
  'models/app_state'
], 
function($, _, Backbone, appModel) { 

  var AppRouter = Backbone.Router.extend({

    initialize : function() {
      // send all routes to URIstack processing function
      this.route(/(.*)/, "toPath", this.toPath);
      this.routesHit = 0;
      Backbone.history.on(
        'route', 
        function(router, route, params)
        {
          this.routesHit++;
          console.log('detected route: ' + route + ', params: ' 
              + JSON.stringify(params) + ', routesHit:' + this.routesHit);
        }, this);

      this.listenTo(appModel, 'change:uriStack', this.uriStackChange);
      console.log('router initialized...');
    },
    
    /** 
     * Pull out complex keys in search - to allow for slashes in the keys
     * recursive to grab multiple search terms 
     **/
    toPath: function(path){
      function popKeys(stack){
        if(!_.isEmpty(stack)){
          var searchIndex = _.indexOf(stack,'search');
          if(searchIndex > -1){
            var newStack = stack.slice(0,searchIndex+1);
            var keys = [];
            stack = stack.slice(searchIndex+1);
            while(!_.isEmpty(stack)){
              var temp = stack.shift();
              if (!_.contains(appModel.LIST_ARGS, temp)){
                keys.push(temp);
              }else{
                stack.unshift(temp);
                break;
              }
            }
            temp = [keys.join('/')];
            newStack = newStack.concat(temp);
            newStack = newStack.concat(popKeys(stack));
            return newStack;
          }
        }
        return stack;
      }
      var uriStack = [];
      if (path){
        uriStack = popKeys(path.split('/'));
      }
      appModel.set({ uriStack: uriStack}, { source: this });
    },
    
    /** 
     * Pull out complex keys in search - to allow for slashes in the keys
     * non-recursive will grab only the first term.
     **/
    toPath1: function(path){
      console.log('toPath: ' + path);
      var uriStack = [];
      if (path){
        uriStack = path.split('/');
        // Pull out complex keys in search - to allow for slashes in the keys
        var searchIndex = _.indexOf(uriStack,'search');
        if(searchIndex > -1){
          var temp = uriStack.slice(0,searchIndex+1);
          // pop search terms off the stack until another router term is found
          var restOfStack = [];
          var searchKey = [];
          var restOfStack = uriStack.slice(searchIndex+1);
          var i = 0;
          for (; i<restOfStack.length; i++){
            var routeKey = restOfStack[i];
            if (!_.contains(appModel.LIST_ARGS, routeKey)){
              searchKey.push(routeKey);
            }else{
              break;
            }
          }
          temp.push(searchKey.join('/'));
          if (i<restOfStack.length){
            temp = temp.concat(restOfStack.slice(i));
          }
          uriStack = temp;
          console.log('new uriStack', uriStack);
        }
      }
      appModel.set({ uriStack: uriStack }, { source: this });
    },
    
    
    
    toPathbak: function(path){
      console.log('toPath: ' + path);
      var uriStack = [];
      if (path){
        uriStack = path.split('/');
        // special search case, search as the last item:
        // - allow for slashes ("/") in the search term
        var searchIndex = _.indexOf(uriStack,'search');
        if(searchIndex > -1){
          var temp = uriStack.slice(0,searchIndex+1);
          temp.push(_.rest(uriStack,searchIndex+1).join('/'));
          uriStack = temp;
          console.log('new uriStack', uriStack);
        }
      }
      appModel.set({ uriStack: uriStack }, { source: this });
    },

    back: function() {  
      if(this.routesHit >= 1) {
        console.log('back, routesHit: ' + this.routesHit);
        // More than one route hit -> user did not land to current page directly
        this.routesHit--;
        window.history.back();
      } else {
        console.log('first route in site, back to home...');
        // Otherwise go to the home page. Use replaceState if available so
        // the navigation doesn't create an extra history entry
        this.navigate('/', {trigger:true, replace:true});
      }
    },

    /**
     * Generate a route that can be used by navigate, from the current state.
     */
    get_route: function(stack){
      return stack.join('/');
    },
    
    uriStackChange: function(model, vals, options){
      if(options.source === this){
        console.log('self generated uristack change');
        return;
      }else{
        this.model_set_route();
      }
    },
    
    model_set_route: function() {
      var uriStack = appModel.get('uriStack');
      console.log('model_set_route: ' + JSON.stringify(uriStack));
      var route = this.get_route(uriStack);
      
      // TODO: this mirrors the handler for route match in main.js
      document.title = 'Screensaver LIMS' + ': ' + route;

      // Trigger false to suppress further parsing, 
      // Replace false (default) to create browser history
      var options = { trigger: false, replace:false }; // , replace:false
      var routing_options = appModel.get('routing_options');
      appModel.set({ routing_options: {} });
      if(!_.isUndefined(routing_options)){
          options = _.extend(options, routing_options);
      }
      
      // Clear out error messages after navigating away from page
      appModel.unset('messages');
      
      this.navigate( route, options );
    }

  });

  return AppRouter;
});